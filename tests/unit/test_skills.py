import pytest

from wrs_agent.runtime import validate_plan
from wrs_agent.schemas import CapabilitySnapshot, Plan, Step
from wrs_agent.skills import SPECS, _rank_candidates, lookup_skills


def caps():
    return {
        "wrs": CapabilitySnapshot(skills=["observe", "move_named_pose"]),
        "tts": CapabilitySnapshot(backend="mock_tts", skills=["speak"]),
    }


def test_aliases_and_capability_filter():
    assert [s.name for s in lookup_skills("播报当前状态", caps())] == ["speak"]
    names = {s.name for s in lookup_skills("把 A 放到 B", caps())}
    assert names == {"observe"}
    assert lookup_skills("pick A", {}) == []


def test_registry_returned_by_copy_and_candidate_cache():
    _rank_candidates.cache_clear()
    first = lookup_skills("移动", caps())
    first[0].aliases.append("untrusted")
    second = lookup_skills("移动", caps())
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
        validate_plan(plan, caps())


def test_skill_contracts_have_readable_metadata_and_bindings():
    assert all(s.description and s.aliases and s.tags and s.node for s in SPECS.values())
    assert SPECS["pick"].recovery == ["observe_once"]
