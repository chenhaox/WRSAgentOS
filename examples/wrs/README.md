# WRS：运动、停止与显示

这里仅放 WRS 本身的例子，全部连接固定 WRS 后端，在 UR7E 模型中执行 IK/FK；不控制实机。

| 文件 | 内容 |
|---|---|
| [01_move.py](01_move.py) | 移动到 B，读取关节与 TCP |
| [02_cancel.py](02_cancel.py) | 运动中取消，等待停止确认 |
| [03_new_action_after_cancel.py](03_new_action_after_cancel.py) | 停止后显式允许新动作，旧计划不续跑 |
| [04_move_relative.py](04_move_relative.py) | 世界坐标系上下左右移动 2 cm |
| [05_start_node.py](05_start_node.py) | 启动独立 WRS 和任务服务，等待其他进程连接 |
| [06_control_arm.py](06_control_arm.py) | 控制已启动的 WRS 节点 |
| [07_viewer.py](07_viewer.py) | 查询场景快照，显示机器人与物体；网页面板调整厨具位置和朝向 |
| [08_scene.py](08_scene.py) | 独立加载场景，读取机器人和物体数据 |
| [09_imported_kitchen.py](09_imported_kitchen.py) | 静态查看默认厨具场景 |
| [10_edit_kitchen.py](10_edit_kitchen.py) | 临时编辑厨具位置和角度；刷新 8000 查看，重启 05 恢复默认 |
| [11_kitchen_skills.py](11_kitchen_skills.py) | 从关节零位播放厨具技能：食物倒进漏勺，入锅静置，捞起抖几下再倒进空碗，最后放回架子 |

01–04 和 08 分别独立运行。05–07 使用同一服务：

```powershell
# 终端一
./scripts/run.ps1 examples/wrs/05_start_node.py
# 终端二
./scripts/run.ps1 examples/wrs/07_viewer.py
# 终端三
./scripts/run.ps1 examples/wrs/06_control_arm.py
```

浏览器打开 http://127.0.0.1:8000。右上角“厨具摆放”可以选厨具、输入位置，并用摇杆或角度框改朝向。修改自动提交，只对本次运行有效；重启 05 恢复默认，并需重新运行 07。viewer 关闭只结束显示。节点重启后重新连接 viewer。

05 每次启动都会复制默认 FAFU 厨具场景，生成本次运行的临时副本。在另一终端运行 `./scripts/run.ps1 examples/wrs/10_edit_kitchen.py`，输入厨具编号、名称或 ID，再输入位置和角度。修改只写入 `.local/kitchen-preview/` 中的运行副本；05 空闲时约 1 秒内读取，刷新 8000 页面即可查看。退出编辑器不会立即还原，但**重启 05 会恢复默认摆放**，并需重新运行 07。默认 [layout.toml](../../wrs_agent/assets/kitchen/layout.toml) 保持不变。详见[厨具摆放指南](../../docs/KITCHEN_LAYOUT.md)。08 仍使用本目录 [scene.toml](scene.toml) 中的工作台与方块，独立执行后自动退出：

~~~powershell
./scripts/run.ps1 examples/wrs/08_scene.py
~~~

snapshot().data 是 SceneData：robot.kinematics 为关节/TCP，objects[id] 包含 pos、rotmat、geometry、来源和有效性；位置单位 m，frame_id 为 world。没有几何的对象不捏造位姿。外层仍保留动作准入、控制版本与 state_version；查询不会触发新的感知。

要看厨具按技能整段播放，在仓库根目录运行：

~~~powershell
python examples/wrs/11_kitchen_skills.py
~~~

浏览器打开 http://127.0.0.1:8008。`--check` 只在终端里规划并核对结果，不打开页面。要在看板上单步选技能，或把目标交给大模型再看它执行，用两个终端：

~~~powershell
# 终端一：留下场景节点和 Agent
python examples/wrs/05_start_node.py
# 终端二：只接看板。Ctrl+C 只关看板
python examples/ui/02_attach.py
~~~

看板是 http://127.0.0.1:8010 ，画面在 http://127.0.0.1:8000 。先停掉占用这两个端口的旧进程，并且不要同时开 07。一条命令同时拉起场景和看板可以用 [03_kitchen_dashboard.py](../ui/03_kitchen_dashboard.py)。完整字段和后续并行节点设计见 [场景说明](../../docs/SCENE_AND_PERCEPTION.md)。

原 08/09 语音例子已移到 [voice/05](../voice/05_start_wrs_voice.py) 和 [voice/06](../voice/06_push_to_talk.py)。语音操作也可以使用这里的 07 显示，但 WRS/05 与 Voice/05 不要同时启动。在线模型的常驻语音是另一组独立地址，见 [Voice 示例](../voice/README.md)。
