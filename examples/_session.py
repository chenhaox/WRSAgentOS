"""Shared credentials for trusted loopback examples, never for network discovery."""

import os
import secrets
import tempfile
from pathlib import Path

_TOKEN_DIR = Path(__file__).resolve().parents[1] / ".local/example_tokens"
_STARTERS = {
    "nodes": "examples/nodes/00_start_router.py 和 01_start_speaker.py",
    "wrs": ("examples/camera/00_start_router.py、examples/wrs/05_start_node.py "
            "或 examples/voice/05_start_wrs_voice.py"),
    "tts": "examples/tts/01_start_node.py",
    "voice": "examples/voice/07_start_llm_voice.py",
}


def _valid(token):
    return 16 <= len(token) <= 128 and token.isascii() and not any(c.isspace() for c in token)


def use_local_token(group, *, create=False):
    """Set this example's environment; an explicit credential always takes precedence."""
    starter = _STARTERS[group]
    configured = os.environ.get("WRS_AGENT_TOKEN")
    if configured:
        if not _valid(configured):
            raise SystemExit(
                "WRS_AGENT_TOKEN 无效：应为 16–128 个非空白 ASCII 字符。"
                "可清除此变量，使用本机示例自动生成的口令。"
            )
        if create:
            print("使用环境变量 WRS_AGENT_TOKEN；配套客户端需配置同一口令。", flush=True)
        return

    path = _TOKEN_DIR / f"{group}.token"
    if create and not path.exists():
        _TOKEN_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Publish a complete file once. Concurrent starters keep the first credential.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", dir=_TOKEN_DIR, delete=False
        ) as temporary:
            temporary.write(secrets.token_urlsafe(32))
        try:
            try:
                os.link(temporary.name, path)
            except FileExistsError:
                pass
        finally:
            Path(temporary.name).unlink()

    try:
        with path.open(encoding="ascii") as source:
            token = source.read(129)
    except FileNotFoundError:
        raise SystemExit(
            f"尚无本机示例口令。请先运行 {starter}，服务会自动生成。"
            "服务若显式配置了 WRS_AGENT_TOKEN，客户端也需要配置同一值。"
        ) from None
    except UnicodeError:
        raise SystemExit(
            f"本机示例口令文件无效：{path}。停止本组示例后删除此文件再启动服务。"
        ) from None
    if not _valid(token):
        raise SystemExit(f"本机示例口令文件无效：{path}。停止本组示例后删除此文件再启动服务。")
    os.environ["WRS_AGENT_TOKEN"] = token
    if create:
        print(
            "已配置本机示例共享口令（不显示内容）；"
            "同一仓库的配套客户端可直接运行，无需设置 WRS_AGENT_TOKEN。",
            flush=True,
        )
