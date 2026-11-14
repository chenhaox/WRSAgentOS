---
name: robot
description: Plan observation, named-pose motion, and supported object handling using the available robot skills.
---

# 机器人技能

根据当前可用技能选择动作。`observe` 只刷新机器人与 Environment 的场景状态，
不读取 CameraNode、不进行物体检测，也没有可绑定到 `speak.text` 的输出。
场景 `objects` 为空不代表相机里没有物体。询问画面中的物体应使用可用的
`describe_objects`；指定目标定位使用 `locate_object`。缺少视觉技能时应说明无法获取画面内容。
`move_named_pose` 用于移动到已知姿态；支持物体操作的节点还可提供
`pick`、`place`、`verify`。具体参数和执行条件见一起提供的 SkillSpec。

把 A 放到 B 的计划通常是：观察，抓取 A，放到 B，检查结果。
用步骤依赖表达顺序；需要决定下一步时使用最新观测。
未找到物体或目标不明确时请求澄清，不能填写想象的坐标。
只有当前能力列表提供相应技能时才可提出该步骤。

WRS 虚拟节点提供观察、命名姿态和 `move_relative`。相对移动以世界坐标系米为单位：上/下为 ±Z，左/右为 ±Y，前/后为 ±X；不是屏幕方向。每次非零位移最多 10 cm，保持末端目标朝向，以关节插值到达目标，不保证笛卡尔直线或避碰。不可达或需切换远处 IK 分支时拒绝执行。`set_gripper` 把 UR7E 末端的 DH50 夹爪完全打开（`open`）或完全闭合（`close`），机械臂不动；闭合只是手指运动，不表示夹住了物体，也不能代替 `pick`。Mock 抓取结果不代表实机接触。
加载了场景的 WRS 虚拟节点还提供 `pick` 和 `place_on`，路径经过碰撞检测。`pick` 只抓场景里正放的长方体：从正上方下降，夹住后抬高 8 cm，观测中的 `held_object` 变为该物体；夹爪张开后两指之间约 2.8 cm，更宽的物体会失败。拿着物体时不能用 `set_gripper`，要用 `place_on` 放下。`place_on` 把手里的物体放到表面正中，表面已有物体或面积不够时失败。抓取成功是虚拟夹持，不代表实机接触。
动作是否成功由节点验证结果确定，不能从“已接收”推断成功。

## 做饭

物体名只用最新观测里出现的名字。容器类物体先确认朝向：倒放的漏网、扣反的锅盖先 `pick`，再 `reorient` 到 `upright`；已经正放的可以省略。
放在表面上（桌面、盘子、灶面）用 `place_on`；放进容器内部或盖上锅用 `seat_in`，不要用 `place_on` 代替，否则会停在边沿上。
煮面示例：`pick(noodles)` → `seat_in(noodles, strainer)` → `pick(strainer)` → `seat_in(strainer, pot)`，后一步依赖前一步。倒放的漏勺仍先 `reorient` 到 `upright` 再放进锅。
`set_control` 开关灶具，执行时夹爪必须为空。
`pour`、`stir`、`scoop`、`shake_off` 由节点根据物体位姿计算并做碰撞检测，执行前后物体都留在夹爪里，需要先 `pick` 源容器或工具；只在节点提供时使用，不能用 `move_relative` 拼出来。容器里的内容物不做仿真，倒和舀的成功只说明动作完成，不说明东西真的倒进去了。
