"""Pure plan, skill and resource-order validation shared by every Runtime entry."""

from graphlib import TopologicalSorter
from itertools import combinations

from wrs_agent.errors import AgentError
from wrs_agent.schemas import ErrorInfo, Plan
from wrs_agent.skills import require_contract, skill_specs


def require_ordered_resources(plan, specs):
    """Steps holding one physical resource must be ordered. A step starts as soon as its own
    dependencies are met, so an undeclared order is settled by whichever coroutine reaches the
    node lock first, and a verification can then observe the motion it was meant to verify."""
    graph = {step.step_id: step.depends_on for step in plan.steps}
    held = {step.step_id: set(specs[step.skill].resources) for step in plan.steps}
    ancestors = {}
    for step_id in TopologicalSorter(graph).static_order():
        ancestors[step_id] = set(graph[step_id]).union(*(ancestors[d] for d in graph[step_id]))
    for first, second in combinations(graph, 2):
        if not held[first] & held[second]:
            continue  # Distinct resources run in parallel by design, such as speech and motion.
        if first not in ancestors[second] and second not in ancestors[first]:
            shared = "、".join(sorted(held[first] & held[second]))
            raise AgentError(
                ErrorInfo(
                    code="unordered_resource_conflict",
                    # 步骤名来自模型，每个可达 80 字符，而 message 是定长字段：
                    # 超长会把一条说得清的计划错误变成 internal_error，所以先截断。
                    message=(
                        f"步骤 {first} 和 {second} 都要占用 {shared}，却没有声明先后关系。"
                        "请给其中一个加 depends_on：同一资源同一时刻只能有一个动作。"
                    )[:240],
                    stage="preflight",
                )
            )


def validate_plan(plan, features, bindings):
    """Resolve versions and check contracts/resources; providers validate Python arguments."""
    plan = Plan.model_validate_json(plan.model_dump_json())
    specs = skill_specs(features, bindings)
    steps = []
    for step in plan.steps:
        node = bindings.get(step.skill)
        if node is None:
            raise AgentError("provider_not_found", stage="preflight")
        cap = features.get(node)
        if cap is None:
            raise AgentError("node_unavailable", node_id=node, stage="preflight")
        require_contract(step.skill, step.version, cap.skills, node_id=node, stage="preflight")
        spec = specs.get(step.skill)
        if spec is None:
            raise AgentError("unsupported_skill_on_current_node", node_id=node, stage="preflight")
        steps.append(step.model_copy(update={"version": spec.version}))
    plan = Plan(steps=steps)
    by_id = {step.step_id: step for step in plan.steps}
    for step in plan.steps:
        target = specs[step.skill]
        for parameter, ref in step.inputs.items():
            if parameter not in target.result_parameters:
                raise AgentError("result_input_not_allowed", stage="preflight")
            source = specs[by_id[ref.step_id].skill]
            output = (source.output or {}).get("properties", {}).get(ref.field)
            argument = target.parameters.get("properties", {}).get(parameter)
            if (not output or not argument or output.get("type") not in
                    {"string", "number", "integer", "boolean"}
                    or output.get("type") != argument.get("type")):
                raise AgentError("incompatible_step_output", stage="preflight")
    require_ordered_resources(plan, specs)
    return plan
