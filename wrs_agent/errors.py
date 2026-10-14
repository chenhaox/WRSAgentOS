"""One error payload shared by local exceptions, RPC failures and task results."""

import sys
from pathlib import Path

# The editor play button runs a file directly and does not use scripts/run.py.
_deps = Path(__file__).resolve().parents[1] / ".local" / "deps"
if _deps.is_dir() and str(_deps) not in sys.path:
    sys.path.insert(0, str(_deps))

from pydantic import ValidationError  # noqa: E402

from wrs_agent.schemas import ErrorInfo  # noqa: E402

_MESSAGES = {
    "llm_invalid_reply": (
        "模型返回的格式不符合所选协议；未创建任务。"
        "请检查 LLM_PROTOCOL 和 LLM_OUTPUT_MODE，确认回复符合所选返回格式。"
    ),
    "llm_unexpected_tool": (
        "模型调用了未提供的工具；未创建任务。"
        "工具模式只接受 propose_plan，JSON 模式不接受工具调用；技能须写在 plan.steps 中。"
    ),
    "llm_plan_json_invalid": (
        "模型返回的计划不是完整合法的 JSON；未创建任务。"
        "请检查模型的输出约束；支持严格结构输出的 Chat 模型可配置 "
        "LLM_OUTPUT_MODE=json_schema，参见语音示例说明。"
    ),
    "llm_plan_invalid": (
        "模型提案不符合计划结构；未创建任务。"
        "请按计划结构填写 kind、plan.steps、depends_on 和 inputs。"
    ),
    "llm_model_missing": (
        "请把 LLM_MODEL 设为账号上已开通的模型 ID；LLM_API_KEY 与 LLM_BASE_URL 不会选择模型。"
        "旧版 GLM_* 变量已改为 LLM_*，见 .env.example。使用 .env 时需让 IDE 运行配置加载该文件。"
    ),
    "llm_model_invalid": (
        "LLM_MODEL 应为 1 到 128 个字符的模型 ID：字母或数字开头，"
        "之后可含字母、数字和 _ . : / @ + -。"
    ),
    "llm_base_url_missing": (
        "请设置 LLM_BASE_URL，例如 https://api.openai.com/v1；不假定任何默认厂商，"
        "各厂商地址见 .env.example。"
    ),
    "llm_base_url_invalid": (
        "LLM_BASE_URL 必须是不含账号、查询串和片段的 https 地址；"
        "明文 http 只允许回环地址（本机 vLLM/Ollama）。"
    ),
    "llm_protocol_invalid": (
        "LLM_PROTOCOL 应为 openai_chat、openai_responses 或 anthropic_messages。"
    ),
    "llm_reasoning_effort_invalid": (
        "LLM_REASONING_EFFORT 应为 none/minimal/low/medium/high/xhigh/max；"
        "留空则由服务端默认。具体模型接受哪些取值以厂商文档为准。"
    ),
    "llm_tool_choice_invalid": "LLM_TOOL_CHOICE 应为 auto 或 required；留空为 auto。",
    "llm_output_mode_invalid": (
        "LLM_OUTPUT_MODE 应为 tools（默认）、json_object 或 json_schema；"
        "两种 JSON 模式仅支持 openai_chat，模型服务须支持所选的输出约束。"
    ),
    "llm_schema_unsupported": "计划 Schema 含无法转换的字典约束；请检查模型适配器。",
    "llm_max_tokens_invalid": "LLM_MAX_TOKENS 应为 128 到 65536 之间的整数。",
    "llm_timeout_s_invalid": "LLM_TIMEOUT_S 应为大于 0、不超过 300 的秒数。",
    "llm_extra_body_invalid": "LLM_EXTRA_BODY 应为不超过 4096 字节的 JSON 对象。",
    "llm_extra_headers_invalid": (
        "LLM_EXTRA_HEADERS 应为 JSON 对象，值为 ASCII 字符串；"
        "不能覆盖 Authorization、x-api-key 等由适配器管理的头。"
    ),
    "llm_proxy_invalid": "LLM_PROXY 应为 http:// 或 https:// 代理地址，不含查询串和片段。",
    "llm_extra_body_conflict": (
        "LLM_EXTRA_BODY 与适配器已生成的字段重名（例如同时设置了 LLM_REASONING_EFFORT）。"
        "只保留一处设置。"
    ),
    "llm_api_key_missing_or_invalid": (
        "请把 LLM_API_KEY 设为不含空白的 API Key；仅回环地址的本地服务可以留空。"
        "使用 .env 时需让 IDE 运行配置加载该文件。"
    ),
    "llm_http_400": (
        "模型服务拒绝了请求参数。常见原因：该模型不支持所设 LLM_REASONING_EFFORT、"
        "LLM_OUTPUT_MODE、LLM_TOOL_CHOICE=required、LLM_EXTRA_BODY 中的字段或 LLM_MAX_TOKENS；"
        "请按该模型支持的参数检查配置。"
    ),
    "llm_http_401": "模型服务拒绝了凭据。请检查 LLM_API_KEY 与 LLM_BASE_URL 是否属于同一服务。",
    "llm_http_404": "模型服务找不到该路径或模型。请检查 LLM_BASE_URL、LLM_PROTOCOL 与 LLM_MODEL。",
    "llm_dns_error": "无法解析模型服务域名。请检查 DNS、网络和 LLM_PROXY。",
    "llm_tls_certificate_error": "HTTPS 证书校验失败。请检查系统时间、证书和 HTTPS 代理配置。",
    "llm_tls_error": "TLS 握手中断。请检查代理路由或 HTTPS 拦截配置。",
    "llm_proxy_error": "代理连接失败。请检查 LLM_PROXY 所指代理是否运行及其地址和端口。",
    "llm_connect_error": "无法建立模型服务连接。请检查网络、LLM_PROXY 和防火墙。",
    "llm_protocol_error": "模型服务或代理未返回完整的 HTTP 响应。",
    "llm_transport_error": "模型网络传输失败。可运行 scripts/check_llm_connection.py 排查。",
    "llm_timeout": "模型请求超时。请检查网络，或调大 LLM_TIMEOUT_S 后重试。",
    # unordered_resource_conflict 不在这张表里：它的正文要点名是哪两个步骤和哪个资源，
    # 由 plan_validation.require_ordered_resources 就地构造，否则这里的通用句会盖掉那些名字。
    "provider_not_found": "No execution provider was discovered or selected for this skill.",
    "node_unavailable": "The node is unavailable in the current discovery view.",
    "node_not_ready": "The node is present but not ready to accept actions.",
    "node_ambiguous": "More than one startup instance claims this node identity.",
    "node_endpoint_ambiguous": "Multiple action nodes claim the same service address.",
    "node_role_ambiguous": "Multiple nodes provide this role; select an explicit peer.",
    "node_role_mismatch": "The selected peer does not provide the required node role.",
    "discovery_capacity_exceeded": "The bounded node directory has reached its capacity.",
    "node_instance_changed": "The execution node startup instance changed.",
    "skill_version_mismatch": "The provider does not implement the required skill version.",
    "skill_not_on_node": "The provider does not implement this skill.",
    "unknown_skill": "This skill is not registered on the execution node.",
    "skill_provider_ambiguous": (
        "多个已发现节点提供同名技能，请通过 skill_bindings 明确指定提供者。"
    ),
    "skill_catalog_changed": "The skill catalog changed during discovery; query it again.",
    "invalid_arguments": "Arguments do not satisfy the skill contract.",
    "invalid_request": "The message does not satisfy the request contract.",
    "invalid_reply": "The reply does not satisfy the response contract.",
    "unauthorized": "This caller is not authorized.",
    "request_timeout": "No response was received within the request deadline.",
    "execution_unknown": "The submitted action's execution outcome cannot be confirmed.",
    "relative_target_unreachable": "No nearby joint-limit-valid IK solution for this offset.",
    "resource_busy": "The execution node is already running an action.",
    "hardware_not_enabled": "节点启动时未允许实机（--allow-hardware），不能切换到实机。",
    "mode_switch_not_ready": "切换前机器人必须空闲、停止已确认，且状态不是 UNKNOWN。",
    "mode_switch_busy": "上一次虚拟/实机切换尚未完成。",
    "hardware_connect_failed": (
        "连接 UR 控制器或夹爪失败：检查网络、示教器远程控制模式和串口；节点保持虚拟模式。"
    ),
    "asr_busy": "This microphone is already capturing another push-to-talk session.",
    "asr_press_finished": "This push-to-talk session already ended; start a new one.",
    "asr_press_not_found": "No push-to-talk session is active with this ID.",
    "internal_error": "The operation failed inside the service.",
}


def error_info(code, **context):
    return ErrorInfo(code=code, message=_MESSAGES.get(code, code.replace("_", " ")), **context)


class AgentError(ValueError):
    """A classified operation failure; inspect error/code instead of parsing str(exc)."""

    def __init__(self, error, **context):
        self.error = error if isinstance(error, ErrorInfo) else error_info(error, **context)
        self.code = self.error.code
        super().__init__(f"{self.code}: {self.error.message}")


def from_exception(exc, *, validation_code="invalid_request", **context):
    if isinstance(exc, AgentError):
        return exc.error.model_copy(
            update={
                key: value
                for key, value in context.items()
                if value is not None and getattr(exc.error, key) is None
            }
        )
    code = (
        "request_timeout"
        if isinstance(exc, TimeoutError)
        else validation_code
        if isinstance(exc, ValidationError)
        else "internal_error"
    )
    # Unknown exceptions and validation errors may include credentials or user input.
    return error_info(code, **context)
