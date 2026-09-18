---
name: speech
description: Plan a spoken status message with the available speak skill while independent robot work continues.
---

# 播报技能

用 `speak` 播报简短信息，文本放在 `text` 参数中。
如果内容不依赖机器人动作结果，播报步骤不必等待机器人完成。
只有已确认的结果才适合表述为“完成”，否则说明正在进行或需要澄清。

播报由独立 TTS 节点执行。取消播报只针对这次播报动作。
当前 Mock TTS 只验证模拟状态，不产生真实音频。
参数和验证约束以一起提供的 SkillSpec 为准。
