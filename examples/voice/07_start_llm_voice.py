"""终端一：启动在线模型语音任务系统；本例可产生模型费用，LLM_* 凭据来自环境。"""

import asyncio
from pathlib import Path

from examples._session import use_local_token
from wrs_agent import ActionState
from wrs_agent.planner.providers.llm import LLMClient, LLMConfig, LLMError
from wrs_agent.processes import LocalStack

CONFIG = Path(__file__).with_name("wrs_bindings.toml")
GREETING = "语音任务系统已启动。"
VOCABULARY = ["机器人", "停止", "停止播报", "状态", "移动到", "初始位置"]


async def main():
    # 先验证配置与凭据；这里只构造/关闭客户端，不发送模型请求。
    try:
        config = LLMConfig.from_env()
        validation = LLMClient(config, live_model=True)
    except LLMError as exc:
        raise SystemExit(f"模型配置错误：{exc.error.message}") from None
    await validation.aclose()
    print(
        f"模型配置：{config.model}｜{config.protocol}｜output_mode={config.output_mode}"
        + (f"｜tool_choice={config.tool_choice}" if config.output_mode == "tools" else ""),
        flush=True,
    )
    print("加载 WRS、Qwen TTS 与 ASR，首次加载模型需要数十秒，启动后会播放提示。", flush=True)
    async with LocalStack(
        backend="wrs", bindings=CONFIG, duration=4.0,
        port=7451, env_id="voice-goal",
        live_model=True,
        tts_backend="qwen", tts_prepared_texts=[GREETING],
        asr_backend="qwen", asr_vocabulary=VOCABULARY,
    ) as stack:
        system = stack.system
        greeting = await system.action("speak", text=GREETING)
        result = await greeting.wait(timeout=30)
        if result.state != ActionState.SUCCEEDED:
            raise RuntimeError(f"启动播报未完成：{result.state}，{result.reason}")
        print("就绪：tcp/127.0.0.1:7451，env voice-goal。", flush=True)
        print(
            "另开终端从仓库根目录运行 examples/voice/09_viewer.py"
            "（http://127.0.0.1:8001，按住空格说话），"
            "或 examples/voice/08_listen_goals.py（循环收音）。",
            flush=True,
        )
        print("两者都用同一支麦克风，请只运行其中一个。", flush=True)
        print("未配置在线模型时直接退出，不会改用 Mock Planner。", flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    use_local_token("voice", create=True)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("在线语音任务系统已关闭。")
