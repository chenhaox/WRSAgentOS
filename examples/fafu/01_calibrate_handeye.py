"""眼在手上标定：仿真臂和桌面上叠实时点云，键盘推到重合，结果写入 handeye.json。

做法同竞赛 demo/hand_eye_calib.py。本脚本自己打开 D405 和机械臂串口：
不要启动 grasping/01，也不要启动 03。电机会松开，先托住机械臂。

浏览器 http://127.0.0.1:8003/ ，点一下页面再按键：
  f 贴桌面        w/s a/d q/e 沿世界 X/Y/Z 平移   z/x c/v b/n 绕世界 X/Y/Z 转
  1/2 平移步长    3/4 旋转步长    p 保存    r 回到进入时的矩阵    8 打印关节角    Esc 退出
先把臂摆到能看见桌面的姿势，先 f 贴桌面，再用 wasd 对齐桌沿和臂本体。
"""

import tomllib
from pathlib import Path

from wrs_agent.nodes.fafu.calibrate import run
from wrs_agent.nodes.fafu.node import ASSETS

# 还没有 handeye.json 时用竞赛的标定作初值（法兰→相机，会换算成 link6→相机）。
SEED_JSON = Path(r"F:\_ref\wrs-competition\demo\handeye_eye_in_hand.json")


def main():
    config = tomllib.loads((ASSETS / "real.toml").read_text(encoding="utf-8"))
    run(config, seed_json=SEED_JSON)


if __name__ == "__main__":
    main()
