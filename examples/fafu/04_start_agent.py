"""Agent：大模型读取识别出的世界，选择 observe / 感知三步 / pick 等技能。"""

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from examples._session import use_local_token
from wrs_agent.nodes.serve import serve_node

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"


async def main(live_model):
    use_local_token(SESSION)
    await serve_node("agent", endpoint=ENDPOINT, env_id=ENV_ID, options={"live_model": live_model})


if __name__ == "__main__":
    load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
    live_model = bool(os.environ.get("LLM_BASE_URL", "").strip()
                      and os.environ.get("LLM_MODEL", "").strip())
    print(f"大模型：{os.environ['LLM_MODEL'].strip()}" if live_model
          else "未配置 LLM_BASE_URL / LLM_MODEL；只能在看板里手动选技能。", flush=True)
    try:
        asyncio.run(main(live_model))
    except KeyboardInterrupt:
        print("Agent 已关闭。")
