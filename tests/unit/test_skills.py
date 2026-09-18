from dataclasses import replace

import pytest
from conftest import action

from wrs_agent.actions import ActionExecutor
from wrs_agent.bindings import load_bindings
from wrs_agent.runtime import validate_plan
from wrs_agent.schemas import CapabilitySnapshot, Empty, Plan, Step
from wrs_agent.skills import SKILLS, Skill, SkillSpec, VirtualWorld, _rank_candidates, lookup_skills

BINDINGS = load_bindings()[1]


def caps():
    return {
        "wrs": CapabilitySnapshot(skills=["observe", "move_named_pose"]),
        "tts": CapabilitySnapshot(backend="mock_tts", skills=["speak"]),
    }


def test_aliases_and_capability_filter():
    assert [s.name for s in lookup_skills("播报当前状态", caps(), BINDINGS)] == ["speak"]
    names = {s.name for s in lookup_skills("把 A 放到 B", caps(), BINDINGS)}
    assert names == {"observe"}
    assert lookup_skills("pick A", {}, BINDINGS) == []


def test_registry_returned_by_copy_and_candidate_cache():
    _rank_candidates.cache_clear()
    first = lookup_skills("移动", caps(), BINDINGS)
    first[0].aliases.append("untrusted")
    second = lookup_skills("移动", caps(), BINDINGS)
    assert "untrusted" not in second[0].aliases
    assert _rank_candidates.cache_info().hits == 1


def test_whole_plan_rejects_unsupported_before_partial_execution():
    plan = Plan(
        steps=[
            Step(step_id="say", skill="speak", args={"text": "starting"}),
            Step(step_id="pick", skill="pick", args={"object": "A"}),
        ]
    )
    with pytest.raises(ValueError, match="unsupported_skill"):
        validate_plan(plan, caps(), BINDINGS)


def test_skill_contracts_describe_abilities_without_deployment_defaults():
    assert all(
        s.description and s.aliases and s.tags for s in (entry.spec for entry in SKILLS.values())
    )
    assert all("node" not in s.model_dump() for s in (entry.spec for entry in SKILLS.values()))
    assert SKILLS["pick"].spec.recovery == ["observe_once"]


@pytest.mark.parametrize("name", ["robot", "speech"])
def test_bundled_guides_are_available_as_package_resources(name):
    from importlib.resources import files

    content = files("wrs_agent.skills").joinpath(name, "SKILL.md").read_text(encoding="utf-8")
    assert content.startswith(f"---\nname: {name}\ndescription: ")
    assert len(content.split("---", 2)) == 3
    assert all(
        s.instructions == content
        for s in (entry.spec for entry in SKILLS.values())
        if (s.name == "speak") == (name == "speech")
    )


def test_missing_binding_cannot_fall_back_to_a_skill_default():
    assert lookup_skills("移动", caps(), {}) == []
    plan = Plan(steps=[Step(step_id="move", skill="move_named_pose", args={"pose": "B"})])
    with pytest.raises(ValueError, match="unsupported_skill"):
        validate_plan(plan, caps(), {})


def test_skill_lookup_and_plan_validation_use_the_same_explicit_binding():
    available = {"robot_lab": caps()["wrs"]}
    bindings = {"move_named_pose": "robot_lab"}
    assert [s.name for s in lookup_skills("移动", available, bindings)] == ["move_named_pose"]
    plan = Plan(steps=[Step(step_id="move", skill="move_named_pose", args={"pose": "B"})])
    assert validate_plan(plan, available, bindings) == plan


async def test_local_registration_drives_validation_capabilities_and_execution(tmp_path):
    calls = []

    def mark(state, args, stop, progress):
        assert isinstance(args, Empty)
        calls.append("mark")
        state.pose = "B"
        return state.pose == "B"

    spec = SkillSpec(
        name="mark",
        description="Reviewed local test handler",
        parameters=Empty.model_json_schema(),
        required_capabilities=["mark"],
        resources=["arm"],
        preconditions=[],
        verification="test_state",
        interrupt_mode="controlled_stop",
    )
    env = ActionExecutor(
        tmp_path / "registered.sqlite3",
        state=VirtualWorld({}),
        skills={"mark": Skill(spec, Empty, mark)},
        backend="test",
        duration=0.01,
    )
    try:
        assert env.capabilities().skills == ["mark"]
        assert env.capabilities().resources == ["arm"]
        assert not (await env.submit(action(env, "pick", {"object": "A"}))).accepted
        assert not (await env.submit(action(env, "mark", {"unexpected": "argument"}))).accepted
        assert calls == []
        request = action(env, "mark", {})
        assert (await env.submit(request)).accepted
        await env.runner
        assert env.status(request.action_id).state == "SUCCEEDED"
        assert (await env.submit(request)).accepted
        assert calls == ["mark"] and env.snapshot().data.pose == "B"
    finally:
        await env.close()


@pytest.mark.parametrize("mismatch", ["name", "schema", "handler"])
def test_invalid_registration_rejected_before_opening_journal(tmp_path, mismatch):
    entry = SKILLS["observe"]
    if mismatch == "name":
        entry = replace(entry, spec=entry.spec.model_copy(update={"name": "wrong"}))
    elif mismatch == "schema":
        entry = replace(entry, arguments=SKILLS["pick"].arguments)
    else:
        entry = replace(entry, handler=None)
    path = tmp_path / "must-not-open.sqlite3"
    with pytest.raises(ValueError, match="invalid_skill_registration"):
        ActionExecutor(path, state=VirtualWorld({}), skills={"observe": entry}, backend="test")
    assert not path.exists()
