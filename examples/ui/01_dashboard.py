"""一个终端启动 WRS 仿真、Agent、UI（可选语音）节点，再在浏览器打开看板；Ctrl+C 全部关闭。"""

import argparse
import asyncio
import webbrowser
from pathlib import Path

from wrs_agent import System

HERE = Path(__file__).parent
SCENE = HERE.parent / "wrs/scene.toml"
UI_PORT = 8010
VIEWER_PORT = 8000
# 只有 --voice mock 使用：每按一次按钮依次“听到”一句，不打开麦克风。
MOCK_SPEECH = ["状态", "回到初始位置", "停止"]
VOCABULARY = ["机器人", "停止", "停止播报", "状态", "移动到", "初始位置"]


async def main(args):
    voice = args.voice != "off"
    async with System.launch(
        backend=args.backend,
        bindings=HERE / ("voice_bindings.toml" if voice else "bindings.toml"),
        scene=SCENE if args.backend == "wrs" else None,
        env_id="ui-demo",
        duration=1.0,
        live_model=args.live_model,
        tts_backend="qwen" if args.voice == "qwen" else "mock",
        asr_backend="qwen" if args.voice == "qwen" else "mock",
        asr_script=MOCK_SPEECH if args.voice == "mock" else (),
        asr_vocabulary=VOCABULARY if args.voice == "qwen" else (),
        ui_port=UI_PORT,
        ui_viewer=args.backend == "wrs",
        ui_viewer_port=VIEWER_PORT,
    ) as system:
        url = f"http://127.0.0.1:{UI_PORT}/"
        nodes = await system.nodes()
        online = ", ".join(f"{name}({row['node_type']})" for name, row in nodes.items())
        print("已上线节点：", online)
        print(f"看板 {url}（WRS 画面 http://127.0.0.1:{VIEWER_PORT}/ 已嵌入右侧）", flush=True)
        print("大模型可以在看板“新建会话”页的“决策大脑”里启用或更换。")
        if not args.no_browser:
            webbrowser.open(url)
        await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["wrs", "mock"], default="wrs")
    parser.add_argument(
        "--live-model", action="store_true",
        help="启动时就用 LLM_* 环境变量启用大模型（也可以之后在看板里启用）",
    )
    parser.add_argument(
        "--voice", choices=["off", "mock", "qwen"], default="off",
        help="mock：按钮按下依次返回预设句子；qwen：本机麦克风 + Qwen 识别/播报"
             "（需先运行 scripts/setup_speech.ps1 并下载模型）",
    )
    parser.add_argument("--no-browser", action="store_true")
    if (args := parser.parse_args()).voice == "qwen":
        print("加载 Qwen TTS 与 ASR，首次加载需要数十秒…", flush=True)
    try:
        asyncio.run(main(args))
    except FileNotFoundError as exc:
        hint = "语音环境未安装；可以先用 --voice mock，或只用浏览器识别。"
        raise SystemExit(f"{exc}\n{hint}") from None
    except KeyboardInterrupt:
        pass
