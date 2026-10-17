"""眼在手上（D405 装在法兰上）手眼标定，和比赛 demo/hand_eye_calib.py 同一套做法。

仿真里画机械臂（实时跟着真机关节角）、桌面和 kitchen_scene 里的厨具长方体，把实时点云
按 ``FK(法兰) @ T_flange_cam`` 变到世界系，用键盘推到和模型重合。产物写到
fafu_config.HANDEYE_JSON（格式和 demo 一样，demo 标好的文件也能直接用）。

    python fafu_real/hand_eye_calib.py
    python fafu_real/hand_eye_calib.py --no-connect --pose look   # 不连真机，用命名位形
    python fafu_real/hand_eye_calib.py --no-autosave

按键：
  w / s  点云沿世界 +X / -X 平移      a / d  +Y / -Y      q / e  +Z / -Z
  z / x  绕世界 X 转                  c / v  绕 Y 转       b / n  绕 Z 转
  f      自动贴桌面：点云主平面摆平并压到 TABLE_Z_M
  1 / 2  平移步长 减半 / 加倍          3 / 4  旋转步长 减半 / 加倍
  p      保存            r  回到进入时的矩阵            8  打印真机关节角和相机位姿

先把臂摆到能看见桌面的观察位（look），先按 f 贴桌面，再用 wasd 对齐桌沿 / 锅 / 盘子，p 保存。
"""

from __future__ import annotations

import argparse
import math
import os

import _paths  # noqa: F401
import fafu_config as C
import handeye
import numpy as np

MOVE_STEP_M = 0.002
ROT_STEP_RAD = np.radians(1.0)
PCD_STRIDE = 4
DESK_BAND_M = 0.05
RBT_SYNC_S = 0.2
SCENE_RGBA = {
    "table": (0.75, 0.72, 0.65, 0.25),
    "surface": (0.95, 0.95, 0.95, 0.5),
    "container": (0.55, 0.55, 0.60, 0.45),
    "food": (0.95, 0.85, 0.45, 0.6),
    "tool": (0.70, 0.45, 0.25, 0.6),
    "appliance": (0.20, 0.20, 0.20, 0.5),
}


def open_sim_gripper(robot, jaw_width: float) -> float:
    ee = robot.end_effector
    jaw_width = float(np.clip(jaw_width, 0.0, C.JAW_OPEN_M))
    jaw_max = max(jaw_width, 0.012)
    ee.jaw_range = np.array([0.0, jaw_max], dtype=float)
    ee.close_bias = 0.0
    ee._command_max = jaw_max
    if hasattr(ee, "jlc") and len(ee.jlc.jnts) > 1:
        ee.jlc.jnts[0].motion_range = np.array([0.0, jaw_max / 2.0], dtype=float)
        ee.jlc.jnts[1].motion_range = np.array([0.0, jaw_max], dtype=float)
    if hasattr(ee, "_calibrate_opening_direction"):
        ee._calibrate_opening_direction()
    return jaw_width


def rotmat_from_axangle(axis, angle: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    n = np.linalg.norm(axis)
    if n < 1e-12:
        return np.eye(3)
    kx, ky, kz = axis / n
    K = np.array([[0.0, -kz, ky], [kz, 0.0, -kx], [-ky, kx, 0.0]], dtype=float)
    return np.eye(3) + np.sin(angle) * K + (1.0 - np.cos(angle)) * (K @ K)


def fit_desk_plane(points_world: np.ndarray):
    pts = np.asarray(points_world, dtype=float).reshape(-1, 3)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) < 80:
        return None, None
    z = pts[:, 2]
    lo, hi = float(np.percentile(z, 5)), float(np.percentile(z, 95))
    if hi - lo < 1e-4:
        return None, None
    hist, edges = np.histogram(z, bins=40, range=(lo, hi))
    peak = int(np.argmax(hist))
    z0 = 0.5 * (edges[peak] + edges[peak + 1])
    band = pts[np.abs(pts[:, 2] - z0) < DESK_BAND_M]
    if len(band) < 50:
        band = pts
    c = band.mean(axis=0)
    _, _, vh = np.linalg.svd(band - c, full_matrices=False)
    normal = vh[-1]
    if normal[2] < 0:
        normal = -normal
    return normal, c


def transform_points(mat, pts) -> np.ndarray:
    pts = np.asarray(pts, dtype=float).reshape(-1, 3)
    ones = np.ones((len(pts), 1), dtype=float)
    return (np.asarray(mat, dtype=float) @ np.hstack((pts, ones)).T).T[:, :3]


def format_deg(q) -> str:
    return "(" + ", ".join(f"{math.degrees(v):.2f}" for v in q) + ")"


def attach_scene(base) -> None:
    """kitchen_scene 里的物体（按 at_deg 算出的位置）画成半透明长方体，用来对点云。"""
    import wrs.modeling.geometric_model as mgm
    from kinematics import Kinematics
    from world import World

    world = World.from_config()
    world.locate_from_joints(Kinematics(np.zeros((6, 2))).tcp, logger=lambda *_: None)
    for item in world.items.values():
        box = mgm.gen_box(
            xyz_lengths=np.asarray(item.size, dtype=float),
            pos=item.pose[:3, 3].copy(),
            rotmat=item.pose[:3, :3].copy(),
            rgb=np.asarray(SCENE_RGBA[item.kind][:3]),
            alpha=SCENE_RGBA[item.kind][3],
        )
        box.attach_to(base)


class EyeInHandCalib:
    def __init__(
        self, base, robot, camera, init_mat, *, out_path, joint_source, jaw_width, autosave=True
    ):
        self.base = base
        self.robot = robot
        self.camera = camera
        self.mat = np.asarray(init_mat, dtype=float).copy()
        self.mat0 = self.mat.copy()
        self.out_path = out_path
        self.joint_source = joint_source
        self.jaw_width = float(jaw_width)
        self.autosave = bool(autosave)
        self.move_step = MOVE_STEP_M
        self.rot_step = ROT_STEP_RAD
        self._node_pcd = None
        self._node_rbt = None
        self._pcd_cam = None
        self._pcd_rgb = None
        self._last_conf = None
        self._dirty = True

        self._bind_keys()
        self._redraw_robot()
        base.taskMgr.doMethodLater(0.5, self._task_grab, "grab frame")
        base.taskMgr.add(self._task_adjust, "adjust calib")
        if self.joint_source is not None:
            base.taskMgr.doMethodLater(RBT_SYNC_S, self._task_sync_rbt, "sync rbt")

    def log(self, msg: str) -> None:
        print(msg)

    def _flange_homomat(self) -> np.ndarray:
        from wrs.basis.robot_math import homomat_from_posrot

        pos = np.asarray(self.robot.manipulator.gl_flange_pos, dtype=float)
        rot = np.asarray(self.robot.manipulator.gl_flange_rotmat, dtype=float)
        return homomat_from_posrot(pos, rot)

    def _w2c(self) -> np.ndarray:
        return self._flange_homomat() @ self.mat

    def _bind_keys(self) -> None:
        self._held: dict[str, bool] = {}
        for key in "wsadqezxcvbn":
            self._held[key] = False
            self.base.inputmgr.keymap.setdefault(key, False)
            self.base.inputmgr.accept(key, self._set_key, [key, True])
            self.base.inputmgr.accept(f"{key}-up", self._set_key, [key, False])
        self.base.inputmgr.accept("f", self._seat_to_desk)
        self.base.inputmgr.accept("p", self._save)
        self.base.inputmgr.accept("r", self._reset)
        self.base.inputmgr.accept("1", self._scale_move, [0.5])
        self.base.inputmgr.accept("2", self._scale_move, [2.0])
        self.base.inputmgr.accept("3", self._scale_rot, [0.5])
        self.base.inputmgr.accept("4", self._scale_rot, [2.0])
        self.base.inputmgr.accept("8", self._print_joints)

    def _set_key(self, key: str, down: bool) -> None:
        self._held[key] = down

    def _scale_move(self, factor: float) -> None:
        self.move_step = float(np.clip(self.move_step * factor, 1e-4, 0.05))
        self.log(f"[calib] 平移步长 {self.move_step * 1000:.2f}mm")

    def _scale_rot(self, factor: float) -> None:
        self.rot_step = float(np.clip(self.rot_step * factor, np.radians(0.05), np.radians(15)))
        self.log(f"[calib] 旋转步长 {np.degrees(self.rot_step):.2f}°")

    def _reset(self) -> None:
        self.mat = self.mat0.copy()
        self._dirty = True
        if self.autosave:
            handeye.save(self.mat, self.out_path)
        self.log("[calib] 已回到进入时的矩阵")

    def _save(self) -> None:
        path = handeye.save(self.mat, self.out_path)
        self.log(f"[calib] 已保存 -> {path}")
        self.log(f"[calib] {handeye.describe(self.mat)}")

    def _print_joints(self) -> None:
        if self.joint_source is None:
            self.log("[calib] 没接真机，没有关节角")
            return
        q = np.asarray(self.joint_source(), dtype=float)
        cam = self._flange_homomat() @ self.mat
        self.log(f"[calib] 关节(°) {format_deg(q)}")
        self.log(
            f"[calib] 相机 xyz [{cam[0, 3]:+.4f}, {cam[1, 3]:+.4f}, {cam[2, 3]:+.4f}] m，"
            f"视线 [{cam[0, 2]:+.3f}, {cam[1, 2]:+.3f}, {cam[2, 2]:+.3f}]"
        )
        self.log("[calib] 记成命名位形：python fafu_real/read_joints.py --save look")

    def _apply_world_delta(self, dT_world: np.ndarray) -> None:
        w2r = self._flange_homomat()
        self.mat = np.linalg.inv(w2r) @ dT_world @ w2r @ self.mat

    def _translate(self, axis_world, amount: float) -> None:
        dT = np.eye(4)
        dT[:3, 3] = np.asarray(axis_world, dtype=float) * float(amount)
        self._apply_world_delta(dT)

    def _rotate(self, axis_world, angle: float) -> None:
        w2r = self._flange_homomat()
        w2c = w2r @ self.mat
        w2c[:3, :3] = rotmat_from_axangle(axis_world, angle) @ w2c[:3, :3]
        self.mat = np.linalg.inv(w2r) @ w2c

    def _seat_to_desk(self) -> None:
        if self._pcd_cam is None:
            self.log("[calib] 还没拿到点云")
            return
        w2c = self._w2c()
        dist = np.linalg.norm(self._pcd_cam, axis=1)
        keep = np.isfinite(self._pcd_cam).all(axis=1) & (dist > 1e-6) & (dist < C.MAX_DEPTH_M)
        if not keep.any():
            self.log("[calib] 有效点太少")
            return
        world = transform_points(w2c, self._pcd_cam[keep])
        normal, centroid = fit_desk_plane(world)
        if normal is None:
            self.log("[calib] 点云太少或没有明显平面，贴桌面失败")
            return
        z = np.array([0.0, 0.0, 1.0])
        axis = np.cross(normal, z)
        angle = float(np.arccos(np.clip(float(np.dot(normal, z)), -1.0, 1.0)))
        if np.linalg.norm(axis) > 1e-9 and abs(angle) > 1e-6:
            dR = rotmat_from_axangle(axis, angle)
            w2c[:3, :3] = dR @ w2c[:3, :3]
            w2c[:3, 3] = centroid + dR @ (w2c[:3, 3] - centroid)
        world = transform_points(w2c, self._pcd_cam[keep])
        _n2, c2 = fit_desk_plane(world)
        dz = float(c2[2] if c2 is not None else centroid[2]) - C.TABLE_Z_M
        w2c[2, 3] -= dz
        self.mat = np.linalg.inv(self._flange_homomat()) @ w2c
        self._dirty = True
        if self.autosave:
            handeye.save(self.mat, self.out_path)
        self.log(
            f"[calib] 贴桌面：摆平 {np.degrees(angle):.2f}°，下压 {dz * 1000:+.1f}mm。"
            "再按几次 f 或用 wasd 对齐桌沿。"
        )

    def _task_adjust(self, task):
        moved = False
        axes = {
            "w": ((1, 0, 0), 1),
            "s": ((1, 0, 0), -1),
            "a": ((0, 1, 0), 1),
            "d": ((0, 1, 0), -1),
            "q": ((0, 0, 1), 1),
            "e": ((0, 0, 1), -1),
        }
        for key, (axis, sign) in axes.items():
            if self._held.get(key):
                self._translate(axis, sign * self.move_step)
                moved = True
        rots = {
            "z": ((1, 0, 0), 1),
            "x": ((1, 0, 0), -1),
            "c": ((0, 1, 0), 1),
            "v": ((0, 1, 0), -1),
            "b": ((0, 0, 1), 1),
            "n": ((0, 0, 1), -1),
        }
        for key, (axis, sign) in rots.items():
            if self._held.get(key):
                self._rotate(axis, sign * self.rot_step)
                moved = True
        if moved:
            self._dirty = True
            if self.autosave:
                handeye.save(self.mat, self.out_path)
        return task.again

    def _task_sync_rbt(self, task):
        try:
            conf = np.asarray(self.joint_source(), dtype=float)
        except Exception as e:
            self.log(f"[calib] 读真机关节角失败：{e}")
            return task.again
        if self._last_conf is not None and np.allclose(conf, self._last_conf, atol=1e-4):
            return task.again
        try:
            self.robot.goto_given_conf(conf, ee_values=self.jaw_width)
        except Exception as e:
            self.log(f"[calib] 同步仿真臂失败：{e}")
            return task.again
        self._last_conf = conf
        self._dirty = True
        self._redraw_robot()
        return task.again

    def _task_grab(self, task):
        try:
            frame = self.camera.capture()
            self._pcd_cam = frame.points.reshape(-1, 3)[::PCD_STRIDE]
            self._pcd_rgb = frame.color.reshape(-1, 3)[::PCD_STRIDE][:, ::-1].astype(float) / 255.0
            self._dirty = True
        except Exception as e:
            self.log(f"[calib] 取帧失败：{e}")
        if self._dirty:
            self._redraw()
            self._dirty = False
        return task.again

    def _redraw_robot(self) -> None:
        if self._node_rbt is not None:
            self._node_rbt.detach()
        self._node_rbt = self.robot.gen_meshmodel(alpha=0.55)
        self._node_rbt.attach_to(self.base)

    def _keep_mask(self, pcd_cam: np.ndarray) -> np.ndarray:
        pts = np.asarray(pcd_cam, dtype=float).reshape(-1, 3)
        dist = np.linalg.norm(pts, axis=1)
        keep = np.isfinite(pts).all(axis=1) & (dist > 1e-6) & (dist < C.MAX_DEPTH_M)
        if not keep.any():
            return keep
        world = transform_points(self._w2c(), pts)
        bounds = C.WORKSPACE_BOUNDS_M
        shown = {
            "x": (bounds["x"][0] - 0.35, bounds["x"][1] + 0.35),
            "y": (bounds["y"][0] - 0.35, bounds["y"][1] + 0.35),
            "z": (-0.15, 0.60),
        }
        for axis, name in enumerate(("x", "y", "z")):
            lo, hi = shown[name]
            keep &= (world[:, axis] >= lo) & (world[:, axis] <= hi)
        return keep

    def _redraw(self) -> None:
        import wrs.modeling.geometric_model as mgm

        if self._node_pcd is not None:
            self._node_pcd.detach()
            self._node_pcd = None
        if self._node_rbt is None:
            self._redraw_robot()
        if self._pcd_cam is None:
            return
        keep = self._keep_mask(self._pcd_cam)
        if not keep.any():
            return
        shown = transform_points(self._w2c(), self._pcd_cam)[keep]
        rgba = np.array([0.25, 0.85, 0.35, 1.0])
        if self._pcd_rgb is not None and len(self._pcd_rgb) == len(keep):
            rgb = self._pcd_rgb[keep]
            rgba = np.hstack((rgb, np.ones((len(rgb), 1))))
        self._node_pcd = mgm.gen_pointcloud(shown, rgba=rgba)
        w2c = self._w2c()
        mgm.gen_frame(w2c[:3, 3], w2c[:3, :3], ax_length=0.08).attach_to(self._node_pcd)
        self._node_pcd.attach_to(self.base)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="眼在手上 D405 手眼标定（法兰→相机）")
    p.add_argument("--out", default=C.HANDEYE_JSON)
    p.add_argument("--resolution", default=C.CAMERA_RESOLUTION, choices=("mid", "high"))
    p.add_argument("--serial", default=C.CAMERA_SERIAL)
    p.add_argument("--port", default=C.SERIAL_PORT)
    p.add_argument("--no-connect", action="store_true", help="不连真机，仿真臂摆在 --pose 命名位形")
    p.add_argument("--pose", default="look", help="--no-connect 或连不上时用的命名位形")
    p.add_argument("--no-autosave", action="store_true", help="只在按 p 时保存")
    args = p.parse_args(argv)

    import wrs.modeling.geometric_model as mgm
    import wrs.visualization.panda.world as wd
    from d405_camera import CameraError, D405Camera
    from wrs.robot_sim.robots.panthera_ht.panthera_ht_single_arm import PantheraHTSglArm

    init_mat = handeye.load_raw(args.out)
    if os.path.isfile(args.out) and not np.allclose(init_mat, np.eye(4)):
        print(f"[calib] 已加载 {args.out}\n[calib] {handeye.describe(init_mat)}")
    else:
        init_mat = np.eye(4)
        print(f"[calib] 没有有效标定，从单位阵开始推。保存到 {args.out}")

    try:
        cam = D405Camera(args.resolution, args.serial)
        cam.open()
    except CameraError as e:
        print(f"[calib] 相机打不开：{e}")
        return 1

    arm, joint_source = None, None
    rest_conf = np.asarray(C.named_pose_rad(args.pose), dtype=float)
    if not args.no_connect:
        from real_arm import RealArm, RealArmError
        from real_config import RealConfigError, load_real_config

        try:
            arm = RealArm(
                load_real_config(),
                dry_run=False,
                speed=1,
                port=args.port,
                use_servo=False,
                release_mode=C.RELEASE_MODE_ON_CLOSE,
            )
            arm.connect()
            joint_source = arm.joint_values
            print(f"[calib] 真机当前关节 {format_deg(arm.joint_values())}")
            print("[calib] 手推机械臂时仿真会跟着动，点云按当前法兰 FK 变换")
        except (RealArmError, RealConfigError, RuntimeError) as e:
            print(
                f"[calib] 连真机失败：{e}\n[calib] 退回命名位形 {args.pose}，点云不会跟真机法兰走"
            )
            arm = None

    base = wd.World(cam_pos=np.array([1.6, -1.2, 1.0]), lookat_pos=np.array([0.3, 0.0, 0.05]))
    try:
        attach_scene(base)
    except Exception as e:
        print(f"[calib] 场景长方体没画上（不影响标定）：{e}")
    mgm.gen_frame(ax_length=0.20).attach_to(base)

    robot = PantheraHTSglArm(enable_cc=False)
    jaw_width = open_sim_gripper(robot, C.JAW_OPEN_M)
    robot.goto_given_conf(
        np.asarray(joint_source(), dtype=float) if joint_source else rest_conf, ee_values=jaw_width
    )

    print(
        "[calib] 操作：f 贴桌面 → wasd/qe 平移 → zxcvbn 旋转 → p 保存\n"
        "[calib] 1/2 平移步长，3/4 旋转步长，8 打印关节角。对准桌面、锅和盘子。"
    )
    if not args.no_autosave:
        print(f"[calib] 每次微调会写入 {os.path.abspath(args.out)}（p 仍可手动存）")

    EyeInHandCalib(
        base,
        robot,
        cam,
        init_mat,
        out_path=args.out,
        joint_source=joint_source,
        jaw_width=jaw_width,
        autosave=not args.no_autosave,
    )
    try:
        base.run()
    finally:
        try:
            cam.close()
        except Exception:
            pass
        if arm is not None:
            try:
                arm.close()
            except Exception as e:
                print(f"[calib] 断开真机失败：{e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
