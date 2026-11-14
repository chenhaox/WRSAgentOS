"""
Panthera-HT 腕部相机（眼在手上）手动标定。

对照 ``manual_calib_piper_eyeinhand_left_camera.py``：同样用键盘把
RealSense 点云贴到仿真臂上，标定矩阵是 TCP → Camera 的 ``T_EC``。

点云变到世界系：

    p_world = T_BE @ T_EC @ p_cam

其中 ``T_BE`` 用真机关节角 + WRS 仿真 FK（与画面里的模型同一套运动学），
不要用 pinocchio ``get_pose``，避免两套 TCP 定义对不齐。

按键：
    r           刷新点云并保存
    w/s a/d q/e 沿世界 X/Y/Z 平移
    z/x c/v b/n 绕世界 X/Y/Z 旋转
"""
__VERSION__ = "0.1.0-panthera"

import atexit
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
from direct.task.TaskManagerGlobal import taskMgr

import wrs.basis.robot_math as rm
import wrs.modeling.geometric_model as mgm
import wrs.robot_sim.robots.robot_interface as ri

_THIS_DIR = Path(__file__).resolve().parent
_DEFAULT_JSON = _THIS_DIR.parent / "manual_calibration_panthera_eyeinhand.json"
_DEFAULT_CFG = (
    Path(__file__).resolve().parents[3]
    / "robot_con"
    / "panthera_ht"
    / "robot.cfg"
)


def py2json_data_formatter(data):
    if isinstance(data, np.ndarray):
        return data.tolist()
    if isinstance(data, (str, float, int, dict)):
        return data
    if isinstance(data, Path):
        return str(data)
    if isinstance(data, list):
        return [py2json_data_formatter(d) for d in data]
    return data


def dump_json(data, path="", reminder=True):
    path = str(path)
    if reminder and os.path.exists(path):
        option = input(f"File {path} exists. Are you sure to write it, y/n: ")
        option_up = option.upper()
        if option_up not in ("Y", "YES"):
            return False
    with open(path, "w") as f:
        json.dump(py2json_data_formatter(data), f)
    return True


def load_calibration_matrix_from_json(filepath):
    with open(filepath, "r") as f:
        data = json.load(f)
    mat_list = data.get("affine_mat", data.get("t_ec"))
    if mat_list is None:
        raise ValueError("JSON 文件中没有找到 'affine_mat' 或 't_ec' 字段")
    return np.array(mat_list, dtype=np.float64)


class ManualCalibrationBase(ABC):
    def __init__(
        self,
        rbt_s: ri.RobotInterface,
        rbt_x,
        sensor_hdl,
        init_calib_mat: rm.np.ndarray = None,
        component_name="arm",
        move_resolution=0.001,
        rotation_resolution=rm.np.radians(5),
        save_path=None,
    ):
        self._rbt_s = rbt_s
        self._rbt_x = rbt_x
        self._sensor_hdl = sensor_hdl
        self._init_calib_mat = np.eye(4) if init_calib_mat is None else np.array(
            init_calib_mat, dtype=np.float64
        )
        self._component_name = component_name
        self._save_path = str(save_path or _DEFAULT_JSON)

        self._plot_node_rbt = None
        self._plot_node_pcd = None
        self._pcd = None

        self._key = {}
        self.map_key()
        self.move_resolution = move_resolution
        self.rotation_resolution = rotation_resolution

        taskMgr.doMethodLater(0.2, self.sync_rbt, "sync rbt")
        taskMgr.add(self.adjust, "manual adjust the mph")

    @abstractmethod
    def get_pcd(self) -> np.ndarray:
        pass

    @abstractmethod
    def get_rbt_jnt_val(self) -> np.ndarray:
        pass

    @abstractmethod
    def align_pcd(self, pcd) -> np.ndarray:
        pass

    def _tcp_homomat(self) -> np.ndarray:
        pos, rot = self._rbt_x.get_pose()
        return rm.homomat_from_posrot(pos, rot)

    def manual_refresh_pcd(self):
        print("Refreshing point cloud")
        self.sync_rbt(None)
        self.sync_pcd(None)

    def move_adjust(self, dir, dir_global, key_name=None):
        w2r_mat = self._tcp_homomat()
        r2w_mat = np.linalg.inv(w2r_mat)
        dir_in_robot_frame = r2w_mat[:3, :3].dot(dir_global)
        self._init_calib_mat[:3, 3] = (
            self._init_calib_mat[:3, 3] + dir_in_robot_frame * self.move_resolution
        )

    def rotate_adjust(self, dir, dir_global, key_name=None):
        w2r_mat = self._tcp_homomat()
        R_w_r = w2r_mat[:3, :3]
        delta_R_world = rm.rotmat_from_axangle(dir_global, self.rotation_resolution)
        R_r_w = R_w_r.T
        delta_R_robot = R_r_w @ delta_R_world @ R_w_r
        current_R_r_c = self._init_calib_mat[:3, :3]
        self._init_calib_mat[:3, :3] = np.dot(delta_R_robot, current_R_r_c)

    def map_key(
        self,
        x="w",
        x_="s",
        y="a",
        y_="d",
        z="q",
        z_="e",
        x_cw="z",
        x_ccw="x",
        y_cw="c",
        y_ccw="v",
        z_cw="b",
        z_ccw="n",
        refresh="r",
    ):
        def add_key(keys):
            if isinstance(keys, str):
                keys = [keys]

            def set_keys(base, k, v):
                base.inputmgr.keymap[k] = v

            for key in keys:
                if key in base.inputmgr.keymap:
                    continue
                base.inputmgr.keymap[key] = False
                base.inputmgr.accept(key, set_keys, [base, key, True])
                base.inputmgr.accept(key + "-up", set_keys, [base, key, False])

        add_key([x, x_, y, y_, z, z_, x_cw, x_ccw, y_cw, y_ccw, z_cw, z_ccw])
        self._key["x"] = x
        self._key["x_"] = x_
        self._key["y"] = y
        self._key["y_"] = y_
        self._key["z"] = z
        self._key["z_"] = z_
        self._key["x_cw"] = x_cw
        self._key["x_ccw"] = x_ccw
        self._key["y_cw"] = y_cw
        self._key["y_ccw"] = y_ccw
        self._key["z_cw"] = z_cw
        self._key["z_ccw"] = z_ccw
        self._key["refresh"] = refresh
        base.inputmgr.accept(refresh, self.manual_refresh_pcd)

    def sync_pcd(self, task):
        self._pcd = self.get_pcd()
        self.plot()
        self.save()

    def sync_rbt(self, task):
        try:
            rbt_jnt_val = self.get_rbt_jnt_val()
            self._rbt_s.fk(rbt_jnt_val, update=True)
            self.plot()
        except Exception as exc:
            print(f"[warn] sync robot failed: {exc}")
        if task is None:
            return None
        return task.again

    def save(self):
        dump_json(
            {
                "affine_mat": self._init_calib_mat.tolist(),
                "t_ec": self._init_calib_mat.tolist(),
            },
            self._save_path,
            reminder=False,
        )
        print(f"[saved] T_EC -> {self._save_path}")

    def plot(self, task=None):
        if self._plot_node_rbt is not None:
            self._plot_node_rbt.detach()
        if self._plot_node_pcd is not None:
            self._plot_node_pcd.detach()
        self._plot_node_rbt = self._rbt_s.gen_meshmodel(alpha=0.3)
        self._plot_node_rbt.attach_to(base)
        pcd = self._pcd
        if pcd is not None:
            if pcd.shape[1] == 6:
                pcd, pcd_color = pcd[:, :3], pcd[:, 3:6]
                pcd_color_rgba = np.append(
                    pcd_color, np.ones((len(pcd_color), 1)), axis=1
                )
            else:
                pcd_color_rgba = np.array([1, 1, 1, 1])
            pcd_r = self.align_pcd(pcd)
            self._plot_node_pcd = mgm.gen_pointcloud(pcd_r, rgba=pcd_color_rgba)
            mgm.gen_frame(
                self._init_calib_mat[:3, 3], self._init_calib_mat[:3, :3]
            ).attach_to(self._plot_node_pcd)
            self._plot_node_pcd.attach_to(base)

    def adjust(self, task):
        was_adjusted = False
        keymap = base.inputmgr.keymap

        if keymap[self._key["x"]]:
            self.move_adjust(dir=self._init_calib_mat[:3, 0], dir_global=np.array([1, 0, 0]))
            was_adjusted = True
        if keymap[self._key["x_"]]:
            self.move_adjust(dir=-self._init_calib_mat[:3, 0], dir_global=np.array([-1, 0, 0]))
            was_adjusted = True
        if keymap[self._key["y"]]:
            self.move_adjust(dir=self._init_calib_mat[:3, 1], dir_global=np.array([0, 1, 0]))
            was_adjusted = True
        if keymap[self._key["y_"]]:
            self.move_adjust(dir=-self._init_calib_mat[:3, 1], dir_global=np.array([0, -1, 0]))
            was_adjusted = True
        if keymap[self._key["z"]]:
            self.move_adjust(dir=self._init_calib_mat[:3, 2], dir_global=np.array([0, 0, 1]))
            was_adjusted = True
        if keymap[self._key["z_"]]:
            self.move_adjust(dir=-self._init_calib_mat[:3, 2], dir_global=np.array([0, 0, -1]))
            was_adjusted = True

        if keymap[self._key["x_cw"]]:
            self.rotate_adjust(dir=self._init_calib_mat[:3, 0], dir_global=np.array([1, 0, 0]))
            was_adjusted = True
        if keymap[self._key["x_ccw"]]:
            self.rotate_adjust(dir=-self._init_calib_mat[:3, 0], dir_global=np.array([-1, 0, 0]))
            was_adjusted = True
        if keymap[self._key["y_cw"]]:
            self.rotate_adjust(dir=self._init_calib_mat[:3, 1], dir_global=np.array([0, 1, 0]))
            was_adjusted = True
        if keymap[self._key["y_ccw"]]:
            self.rotate_adjust(dir=-self._init_calib_mat[:3, 1], dir_global=np.array([0, -1, 0]))
            was_adjusted = True
        if keymap[self._key["z_cw"]]:
            self.rotate_adjust(dir=self._init_calib_mat[:3, 2], dir_global=np.array([0, 0, 1]))
            was_adjusted = True
        if keymap[self._key["z_ccw"]]:
            self.rotate_adjust(dir=-self._init_calib_mat[:3, 2], dir_global=np.array([0, 0, -1]))
            was_adjusted = True

        if was_adjusted:
            self.plot()
            self.save()
        return task.again


class PantheraCalibArm:
    """真机读关节，仿真 FK 给出 TCP 位姿，接口对齐 PiperArmController。"""

    def __init__(self, hw, rbt_s):
        self._hw = hw
        self._rbt_s = rbt_s

    def get_joint_values(self) -> np.ndarray:
        q = np.asarray(self._hw.get_joint_values(), dtype=float).reshape(-1)[:6]
        return q

    def get_pose(self):
        q = self.get_joint_values()
        self._rbt_s.fk(jnt_values=q, update=True)
        return np.array(self._rbt_s.gl_tcp_pos), np.array(self._rbt_s.gl_tcp_rotmat)

    def close_connection(self):
        if hasattr(self._hw, "close_connection"):
            self._hw.close_connection()


class PantheraEyeInHandCalib(ManualCalibrationBase):
    """Eye-in-hand：标定矩阵是 TCP → Camera。"""

    def get_pcd(self):
        pcd, pcd_color, _, _ = self._sensor_hdl.get_pcd_texture_depth()
        return np.hstack((pcd, pcd_color))

    def get_rbt_jnt_val(self):
        return self._rbt_x.get_joint_values()

    def align_pcd(self, pcd):
        r2cam_mat = self._init_calib_mat
        w2r_mat = self._tcp_homomat()
        w2c_mat = w2r_mat.dot(r2cam_mat)
        return rm.transform_points_by_homomat(w2c_mat, points=pcd)


if __name__ == "__main__":
    import argparse

    import wrs.visualization.panda.world as wd
    from wrs.drivers.devices.realsense.realsense_d400s import RealSenseD405
    from wrs.robot_con.panthera_ht.fafu_robot_controller import FafuRobotController
    from wrs.robot_sim.robots.panthera_ht.panthera_ht_single_arm import PantheraHTSglArm

    parser = argparse.ArgumentParser(description="Panthera 腕部相机眼在手上手动标定")
    parser.add_argument("--cfg", default=str(_DEFAULT_CFG), help="robot.cfg 路径")
    parser.add_argument("--port", default=None, help="串口，默认用 cfg 里的 auto")
    parser.add_argument("--device", default=None, help="RealSense 序列号，多相机时指定腕部那台")
    parser.add_argument("--init-json", default=str(_DEFAULT_JSON), help="初值 / 结果 JSON")
    parser.add_argument("--gripper-id", type=int, default=7)
    parser.add_argument("--move-res", type=float, default=0.0005, help="平移步长 (m)")
    parser.add_argument("--rot-res-deg", type=float, default=4.0, help="旋转步长 (deg)")
    args = parser.parse_args()

    save_path = Path(args.init_json)
    if save_path.exists():
        init_mat = load_calibration_matrix_from_json(save_path)
        print(f"加载初值: {save_path}")
    else:
        init_mat = np.eye(4)
        print(f"未找到 {save_path}，从单位阵开始")

    base = wd.World(cam_pos=[0, 2, 0.6], lookat_pos=[0, 0, 0.2], lens_type=2)
    mgm.gen_frame(ax_length=1).attach_to(base)

    rbt_s = PantheraHTSglArm(enable_cc=False, name="panthera_left")

    print("正在连接 Panthera 真机（失能，可手拖）...")
    hw = FafuRobotController(
        cfg_path=args.cfg,
        port=args.port,
        has_gripper=True,
        gripper_motor_id=args.gripper_id,
        auto_enable=False,
        auto_polling=True,
    )
    try:
        hw.disable()
    except Exception as exc:
        print(f"[warn] disable() 失败，手臂可能仍有力矩: {exc}")

    rbt_x = PantheraCalibArm(hw=hw, rbt_s=rbt_s)
    atexit.register(rbt_x.close_connection)

    print("正在连接 RealSense D405...")
    rs_pipe = RealSenseD405(resolution="high", device=args.device)
    rs_pipe.get_pcd_texture_depth()
    rs_pipe.get_pcd_texture_depth()

    print(
        "\n操作说明:\n"
        "  手拖机械臂换视角，按 r 刷新点云\n"
        "  w/s a/d q/e 平移，z/x c/v b/n 旋转\n"
        "  点云与仿真臂对齐后矩阵已自动写入 JSON\n"
        f"  保存路径: {save_path}\n"
    )

    PantheraEyeInHandCalib(
        rbt_s=rbt_s,
        rbt_x=rbt_x,
        sensor_hdl=rs_pipe,
        init_calib_mat=init_mat,
        move_resolution=args.move_res,
        rotation_resolution=np.radians(args.rot_res_deg),
        save_path=save_path,
    )
    base.run()
