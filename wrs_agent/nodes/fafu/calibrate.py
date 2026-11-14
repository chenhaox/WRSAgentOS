"""Eye-in-hand calibration by pushing the live point cloud onto the arm model.

Same procedure as the competition's ``demo/hand_eye_calib.py``: the arm is posed by
hand, the D405 cloud is drawn in the world through the current matrix, and the
keyboard moves it until the table and the arm line up. The parent frame is the
planner's URDF ``link6``, so ``handeye.json`` loads unchanged in the FAFU node.

This process owns the D405 and the serial port: the camera node and the FAFU node
must not be running.
"""

import json
import time
import webbrowser
from pathlib import Path

import numpy as np

from wrs_agent.drivers.cameras.realsense import RealSenseCamera
from wrs_agent.env.fafu_backend import FafuModel
from wrs_agent.env.viewer import viewer_hub
from wrs_agent.nodes.fafu import handeye
from wrs_agent.nodes.fafu.arm import FafuArm

VIEWER_PORT = 8003
MOVE_STEP_M = 0.002
ROT_STEP_RAD = np.radians(1.0)
PCD_STRIDE = 4
MAX_DEPTH_M = 1.2
DESK_BAND_M = 0.05
GRAB_S = 0.5
TICK_S = 0.05
# Crop in the world so the page is not flooded by the room behind the table.
SHOWN = {"x": (-0.30, 1.05), "y": (-0.80, 0.80), "z": (-0.15, 0.60)}
# WRS PantheraHT flange = URDF link6 · R_y(+90°); converts competition matrices.
FLANGE_IN_LINK6 = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]])


def rotmat_from_axangle(axis, angle):
    axis = np.asarray(axis, dtype=float)
    n = np.linalg.norm(axis)
    if n < 1e-12:
        return np.eye(3)
    kx, ky, kz = axis / n
    k = np.array([[0.0, -kz, ky], [kz, 0.0, -kx], [-ky, kx, 0.0]])
    return np.eye(3) + np.sin(angle) * k + (1.0 - np.cos(angle)) * (k @ k)


def fit_desk_plane(points_world):
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


def competition_seed(path):
    """``affine_mat`` (WRS flange -> camera) from the competition, as link6 -> camera."""
    path = Path(path)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    mat = np.asarray(data.get("affine_mat"), dtype=float)
    if mat.shape != (4, 4) or np.allclose(mat, np.eye(4)):
        return None
    seed = np.eye(4)
    seed[:3, :3] = FLANGE_IN_LINK6 @ mat[:3, :3]
    seed[:3, 3] = FLANGE_IN_LINK6 @ mat[:3, 3]
    return seed


class EyeInHandCalib:
    def __init__(self, base, model, arm, camera, init_mat):
        import wrs.scene.scene_object_primitive as primitives
        import wrs.viewer.key as keys

        self.base, self.model, self.arm, self.camera = base, model, arm, camera
        self.primitives, self.keys = primitives, keys
        self.mat = np.asarray(init_mat, dtype=float).copy()
        self.mat0 = self.mat.copy()
        self.move_step = MOVE_STEP_M
        self.rot_step = ROT_STEP_RAD
        self.link = next(item for item in model.robot.runtime_lnks if item.name == "link6")
        self._pcd_cam = self._pcd_rgb = None
        self._cloud = None
        self._last_q = None
        self._last_grab = 0.0
        self._dirty = True
        desk = primitives.plane(pos=(0.30, 0.0, -0.0005), size=(1.2, 1.0),
                                rgb=(0.55, 0.55, 0.55), alpha=0.25)
        model.scene.add(desk)

    def log(self, msg):
        print(msg, flush=True)

    def _link6(self):
        return np.asarray(self.link.tf, dtype=float)

    def _w2c(self):
        return self._link6() @ self.mat

    def _save(self, reason=None):
        handeye.save(self.mat)
        if reason:
            t = np.round(self.mat[:3, 3], 4).tolist()
            self.log(f"[calib] {reason}，link6→相机平移 {t} m，已写入 {handeye.PATH}")

    # ---------------------------------------------------------------- keys

    def _scale_move(self, factor):
        self.move_step = float(np.clip(self.move_step * factor, 1e-4, 0.05))
        self.log(f"[calib] 平移步长 {self.move_step * 1000:.2f}mm")

    def _scale_rot(self, factor):
        self.rot_step = float(np.clip(self.rot_step * factor, np.radians(0.05), np.radians(15)))
        self.log(f"[calib] 旋转步长 {np.degrees(self.rot_step):.2f}°")

    def _reset(self):
        self.mat = self.mat0.copy()
        self._dirty = True
        self._save("已回到进入时的矩阵")

    def _print_joints(self):
        q = np.degrees(np.asarray(self.arm.joints(), dtype=float))
        self.log("[calib] 关节角（度）" + ", ".join(f"{v:.2f}" for v in q))
        cam = self._w2c()[:3, 3]
        self.log(f"[calib] 相机在世界 {np.round(cam, 3).tolist()} m")

    def _translate(self, axis_world, amount):
        w2r = self._link6()
        dT = np.eye(4)
        dT[:3, 3] = np.asarray(axis_world, dtype=float) * float(amount)
        self.mat = np.linalg.inv(w2r) @ dT @ w2r @ self.mat

    def _rotate(self, axis_world, angle):
        w2r = self._link6()
        w2c = w2r @ self.mat
        w2c[:3, :3] = rotmat_from_axangle(axis_world, angle) @ w2c[:3, :3]
        self.mat = np.linalg.inv(w2r) @ w2c

    def _valid_cam(self):
        dist = np.linalg.norm(self._pcd_cam, axis=1)
        return np.isfinite(self._pcd_cam).all(axis=1) & (dist > 1e-6) & (dist < MAX_DEPTH_M)

    def _seat_to_desk(self):
        if self._pcd_cam is None:
            self.log("[calib] 还没拿到点云")
            return
        keep = self._valid_cam()
        if not keep.any():
            self.log("[calib] 有效点太少")
            return
        w2c = self._w2c()
        world = handeye.transform(w2c, self._pcd_cam[keep])
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
        world = handeye.transform(w2c, self._pcd_cam[keep])
        _, c2 = fit_desk_plane(world)
        dz = float(c2[2] if c2 is not None else centroid[2])
        w2c[2, 3] -= dz
        self.mat = np.linalg.inv(self._link6()) @ w2c
        self._dirty = True
        self._save(f"贴桌面：摆平 {np.degrees(angle):.2f}°，下压 {dz * 1000:+.1f}mm。"
                   "再按几次 f 或用 wasd 对齐桌沿")

    def _handle_keys(self):
        edge, held, k = self.base.is_key_pressed_edge, self.base.is_key_pressed, self.keys
        if edge(k.F):
            self._seat_to_desk()
        if edge(k.P):
            self._save("已保存")
        if edge(k.R):
            self._reset()
        if edge(ord("1")):
            self._scale_move(0.5)
        if edge(ord("2")):
            self._scale_move(2.0)
        if edge(ord("3")):
            self._scale_rot(0.5)
        if edge(ord("4")):
            self._scale_rot(2.0)
        if edge(ord("8")):
            self._print_joints()
        if edge(k.ESCAPE):
            self.base.close()
        moved = False
        for symbol, axis, sign in ((k.W, (1, 0, 0), 1), (k.S, (1, 0, 0), -1),
                                   (k.A, (0, 1, 0), 1), (k.D, (0, 1, 0), -1),
                                   (k.Q, (0, 0, 1), 1), (k.E, (0, 0, 1), -1)):
            if held(symbol):
                self._translate(axis, sign * self.move_step)
                moved = True
        for symbol, axis, sign in ((k.Z, (1, 0, 0), 1), (k.X, (1, 0, 0), -1),
                                   (k.C, (0, 1, 0), 1), (k.V, (0, 1, 0), -1),
                                   (k.B, (0, 0, 1), 1), (k.N, (0, 0, 1), -1)):
            if held(symbol):
                self._rotate(axis, sign * self.rot_step)
                moved = True
        if moved:
            self._dirty = True
            self._save()

    # ---------------------------------------------------------------- tick

    def _sync_robot(self):
        try:
            q = np.asarray(self.arm.joints(), dtype=float)
        except Exception as exc:
            self.log(f"[calib] 读真机关节角失败：{exc}")
            return
        if self._last_q is not None and np.allclose(q, self._last_q, atol=1e-4):
            return
        limits = self.model.limits
        self.model.apply(np.clip(q, limits[:, 0], limits[:, 1]))
        self._last_q = q
        self._dirty = True

    def _grab(self):
        now = time.monotonic()
        if now - self._last_grab < GRAB_S:
            return
        self._last_grab = now
        try:
            frame = self.camera.read()
        except Exception as exc:
            self.log(f"[calib] 取帧失败：{exc}")
            return
        self._pcd_cam = np.asarray(frame.points, dtype=float).reshape(-1, 3)[::PCD_STRIDE]
        self._pcd_rgb = np.asarray(frame.rgb).reshape(-1, 3)[::PCD_STRIDE].astype(float) / 255.0
        self._dirty = True

    def _redraw(self):
        if self._cloud is not None:
            self.model.scene.remove(self._cloud)
            self._cloud = None
        if self._pcd_cam is None:
            return
        keep = self._valid_cam()
        if not keep.any():
            return
        world = handeye.transform(self._w2c(), self._pcd_cam)
        for axis, name in enumerate(("x", "y", "z")):
            lo, hi = SHOWN[name]
            keep &= (world[:, axis] >= lo) & (world[:, axis] <= hi)
        if not keep.any():
            return
        self._cloud = self.primitives.point_cloud(world[keep], self._pcd_rgb[keep])
        self.model.scene.add(self._cloud)

    def tick(self, _dt):
        self._handle_keys()
        self._sync_robot()
        self._grab()
        if self._dirty:
            self._redraw()
            self._dirty = False


def run(config, *, seed_json=None):
    try:
        matrix = handeye.load()
        if np.allclose(matrix, np.eye(4)):
            raise handeye.HandEyeError("identity")
        print(f"[calib] 已加载 {handeye.PATH}")
    except handeye.HandEyeError:
        matrix = competition_seed(seed_json) if seed_json else None
        if matrix is not None:
            print(f"[calib] 没有有效标定，用竞赛标定换算成 link6 作初值：{seed_json}")
        else:
            matrix = np.eye(4)
            print("[calib] 没有有效标定，从单位阵开始推")

    camera = RealSenseCamera(width=848, height=480, fps=15)
    try:
        camera.open()
        camera.read()
    except Exception as exc:
        camera.close()
        raise SystemExit(
            f"相机打不开：{exc}\n请先关掉正在占用 D405 的节点（examples.grasping.01_start_camera）。"
        ) from None

    arm = None
    try:
        model = FafuModel()
        import wrs.viewer.world as world

        # World would spawn `python -m wrs.viewer.server`, which cannot import
        # third_party/wrs; serve the page here, before the motors are released.
        with viewer_hub(VIEWER_PORT):
            url = f"http://127.0.0.1:{VIEWER_PORT}/"
            webbrowser.open(url)
            arm = FafuArm(config, execute=True)
            arm.connect()
            arm.release()
            print(f"[calib] 电机已松开，先用手托住机械臂。页面 {url} ，点一下页面再按键。")
            print(
                "[calib] 操作：f 贴桌面 → wasd/qe 平移 → zxcvbn 旋转 → p 保存\n"
                "[calib] 1/2 平移步长，3/4 旋转步长，r 回到进入时的矩阵，8 打印关节角，Esc 退出\n"
                f"[calib] 每次微调都会写入 {handeye.PATH}"
            )
            base = world.World(
                cam_pos=(1.0, -0.9, 0.7), cam_lookat_pos=(0.2, 0.0, 0.1),
                port=VIEWER_PORT, auto_start_hub=False,
            )
            base.set_scene(model.scene)
            base.set_caption("FAFU 眼在手上标定")
            calib = EyeInHandCalib(base, model, arm, camera, matrix)
            base.schedule_interval(calib.tick, TICK_S)
            base.run()
    finally:
        if arm is not None:
            print("[calib] 正在锁住电机…")
            try:
                arm.capture()
            except Exception as exc:
                print(f"[calib] 重新上使能失败：{exc}")
            arm.close()
        camera.close()
        print(f"[calib] 结束。矩阵在 {handeye.PATH}")
