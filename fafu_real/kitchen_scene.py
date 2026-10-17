"""场景里有哪些东西、各在哪。现在不接视觉，位置用关节角记录。

每个物体只填 ``at_deg``：两指中间（TCP）对准它时的 6 个关节角（度，J1~J6）。
- 能抓的（面条、漏勺、勺子）：对准要夹的位置，夹爪摆成你想夹的方向。
- 不能抓的（锅、盘子、灶）：TCP 对准顶面中心。
- 灶：TCP 对准开关按键。

记法：摆好后运行 ``python fafu_real/read_joints.py``，把打印的
``"at_deg": (...)`` 那一行贴到对应物体下面。

程序启动时用正运动学把关节角换成世界坐标，Agent / 技能就知道每个东西在哪，
然后按指令自己选 pick / seat_in / pour 等技能完成动作。
``at_deg`` 填 None 的物体用程序里的占位位置（只给离线 check 用）。
以后视觉接上，物体位置由识别结果给出，这个文件就不用了。

夹爪收多紧在 fafu_config.GRIP。物体大小程序里有粗略默认值，不用量。
"""

from __future__ import annotations

OBJECTS = {
    "table": {"label": "桌面", "kind": "table"},
    "cooktop": {"label": "电磁炉", "kind": "appliance", "at_deg": None},
    "pot": {"label": "煮面锅", "kind": "container", "at_deg": None},
    "strainer": {"label": "漏勺", "kind": "container", "at_deg": None},
    "noodles": {"label": "面条", "kind": "food", "at_deg": None},
    "plate": {"label": "盘子", "kind": "surface", "at_deg": None},
    "spoon": {"label": "汤勺", "kind": "tool", "at_deg": None},
}
# 例：
#     "noodles": {"label": "面条", "kind": "food",
#                 "at_deg": (9.33, 71.74, 78.36, -85.76, 0.41, 13.11)},
