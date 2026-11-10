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
    "segmentation_source_timeout": (
        "分割尚未开始：连接或读取上游图像/定位数据超时。请检查故障所示节点是否可达及其负载。"
    ),
    "segmentation_inference_failed": "分割推理未完成，请检查模型环境和 GPU 状态。",
    "segmentation_gpu_out_of_memory": "分割推理时 GPU 显存不足，请减少其他模型的显存占用。",
    "invalid_segmentation_output": "分割结果格式或数值无效，不能生成目标掩码。",
    "segmentation_grid_mismatch": "分割掩码尺寸与原始 RGB 不一致，不能匹配深度和点云。",
    "region_mask_not_found": "FastSAM 未找到与 YOLO 定位框充分重合的掩码，请检查定位框和画面。",
    "region_mask_ambiguous": "多个分割掩码与定位框匹配程度接近，无法唯一确定目标。",
    "target_not_found": "当前画面未找到满足条件的目标。",
    "target_ambiguous": "当前画面有多个符合条件的目标，请明确目标或指定方位。",
    "too_many_segment_candidates": "分割候选数量达到上限，请减少画面杂物或调整视野。",
    "camera_stale": "相机观测已过期，请检查相机采集是否持续更新。",
    "segment_not_available": "暂无有效分割结果，请先完成一次分割任务。",
    "segment_changed": "分割结果已变化，请读取最新结果。",
    "artifact_type_mismatch": (
        "步骤引用的结果种类不匹配。抓姿预测需要分割结果 segment_id；"
        "请先定位、再分割，不能用 location_id 或 observation_id 代替分割结果。"
    ),
    "segmentation_unavailable": (
        "GraspNet 无法读取分割结果：分割节点未就绪或不可达。"
        "请启动 FastSAM，并确认节点连接同一环境；修改配置后需重启节点。"
    ),
    "observation_stale": "观测已过期，请重新进行定位和分割后再预测抓姿。",
    "artifact_expired": "本次任务的数据已过期或被清除，请重新执行完整感知流程。",
    "artifact_task_mismatch": "结果属于另一任务，不能用于本次推理。",
    "artifact_invalidated": "结果因取消、节点重启或动作状态变化而失效。",
    "insufficient_target_depth": (
        "分割区域内有效三维点少于 64 个，无法预测抓姿。"
        "请检查目标深度、相机距离及掩码与点云是否对齐。"
    ),
    "no_grasp_within_gripper_width": "没有符合当前夹爪最大开口的有效抓取候选。",
    "invalid_graspnet_output": "GraspNet 返回的抓姿数据不符合预期格式或含无效数值。",
    "no_grasp_in_target_roi": (
        "GraspNet 已完成场景推理，但分割目标区域内没有抓取候选。请检查目标分割和有效深度。"
    ),
    "grasp_roi_not_sampled": (
        "全场景降采样没有保留目标区域的点，尚不能判断该目标的抓取候选。"
    ),
    "grasp_roi_no_seed": (
        "输入包含目标点，但 GraspNet 选取的抓取种子没有覆盖目标区域。"
    ),
    "grasp_roi_rejected_as_background": (
        "目标区域有抓取种子，但模型的物体/背景判定将它们全部判为背景，解码未保留目标候选。"
    ),
    "grasp_inference_failed": "GraspNet 推理未完成，请检查模型环境和 GPU 状态。",
    "grasp_gpu_out_of_memory": "GraspNet 推理时 GPU 显存不足，请减少其他模型的显存占用。",
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
    "fafu_not_connected": "Fafu 机械臂尚未连接。先把机器人模式切到 real，再发运动指令。",
    "fafu_named_pose_unsupported": "Fafu 臂只接受命名姿态 home。B 和 C 是 UR7e 模型姿态。",
    "fafu_kinematics_unavailable": "Fafu 运动学模型没有加载，不能做笛卡尔相对移动。",
    "fafu_joint_count": "Fafu 反馈的关节数不是 6，不能写入当前机器人状态。",
    "fafu_connect_failed": (
        "连接 Fafu 调试板失败：检查 USB 串口、电源和电机应答；节点保持断开。"
    ),
    "fafu_hand_eye_requires_measured_calibration": (
        "Fafu 手眼标定必须是实测外参。虚拟夹具不能用来换算实机抓取点。"
    ),
    "asr_busy": "This microphone is already capturing another push-to-talk session.",
    "asr_press_finished": "This push-to-talk session already ended; start a new one.",
    "asr_press_not_found": "No push-to-talk session is active with this ID.",
    "internal_error": "The operation failed inside the service.",
}


def error_info(code, **context):
    return ErrorInfo(code=code, message=_MESSAGES.get(code, code.replace("_", " ")), **context)


def known_error_info(code):
    """Only reviewed codes may turn an exception string into a public explanation."""
    return error_info(code) if code in _MESSAGES else None


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
