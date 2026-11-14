import argparse
import asyncio
import webbrowser
from pathlib import Path

from wrs_agent import System
from wrs_agent.env.wrs import ROOT

HERE = Path(__file__).parent
SCENE = ROOT / "wrs_agent/assets/kitchen/scene.toml"
UI_PORT = 8010
VIEWER_PORT = 8000
MOCK_SPEECH = ["把碗里的食物倒进漏勺", "停止"]
VOCABULARY = ["机器人", "停止", "漏勺", "碗", "倒", "煮面锅"]


async def main(args):
    voice = args.voice != "off"
    async with System.launch(
        backend="wrs",
        bindings=HERE / ("voice_bindings.toml" if voice else "bindings.toml"),
        scene=SCENE,
        env_id="kitchen-ui",
        duration=1.0,
        live_model=args.live_model,
        tts_backend="qwen" if args.voice == "qwen" else "mock",
        asr_backend="qwen" if args.voice == "qwen" else "mock",
        asr_script=MOCK_SPEECH if args.voice == "mock" else (),
        asr_vocabulary=VOCABULARY if args.voice == "qwen" else (),
        ui_port=UI_PORT,
        ui_viewer=True,
        ui_viewer_port=VIEWER_PORT,
    ) as system:
        url = f"http://127.0.0.1:{UI_PORT}/"
        nodes = await system.nodes()
        online = ", ".join(f"{name}({row['node_type']})" for name, row in nodes.items())
        print("已上线节点：", online, flush=True)
        print(f"看板 {url}（厨具画面 http://127.0.0.1:{VIEWER_PORT}/ 已嵌入右侧）", flush=True)
        print("技能在看板里单步执行。大模型在“新建会话”页的“决策大脑”里启用，对话后由它自己排步骤。", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-model", action="store_true",
        help="启动时就用 LLM_* 环境变量启用大模型（也可以之后在看板里启用）",
    )
    parser.add_argument(
        "--voice", choices=["off", "mock", "qwen"], default="off",
        help="mock：按钮按下依次返回预设句子；qwen：本机麦克风 + Qwen 识别/播报",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.voice == "qwen":
        print("加载 Qwen TTS 与 ASR，首次加载需要数十秒…", flush=True)
    try:
        asyncio.run(main(args))
    except FileNotFoundError as exc:
        hint = "语音环境未安装；可以先用 --voice mock，或只用浏览器识别。"
        raise SystemExit(f"{exc}\n{hint}") from None
    except KeyboardInterrupt:
        pass
