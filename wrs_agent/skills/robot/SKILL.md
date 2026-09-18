---
name: robot
description: Plan observation, named-pose motion, and supported object handling using the available robot skills.
---

# 机器人技能

根据当前可用技能选择动作。`observe` 用于读取当前证据，
`move_named_pose` 用于移动到已知姿态；支持物体操作的节点还可提供
`pick`、`place`、`verify`。具体参数和执行条件见一起提供的 SkillSpec。

把 A 放到 B 的计划通常是：观察，抓取 A，放到 B，检查结果。
用步骤依赖表达顺序；需要决定下一步时使用最新观测。
未找到物体或目标不明确时请求澄清，不能填写想象的坐标。
只有当前能力列表提供相应技能时才可提出该步骤。

WRS FK 虚拟节点目前只提供观察和命名姿态；Mock 抓取结果不代表实机接触。
动作是否成功由节点验证结果确定，不能从“已接收”推断成功。
