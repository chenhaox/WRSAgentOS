"""Offline tests of the real-camera example's failure paths and task-bound result reading."""

import runpy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def example(monkeypatch):
    main = runpy.run_path("examples/grasping/06_propose_grasps.py")["main"]
    completed = SimpleNamespace(state="SUCCEEDED", reason="")
    task = SimpleNamespace(id="this-task", wait=AsyncMock(return_value=completed))
    action = SimpleNamespace(state="SUCCEEDED", output={
        "candidate_set_id": "this-candidate", "observation_id": "this-frame", "count": 1,
    })
    observation = SimpleNamespace(source="realsense", observation_id="this-frame",
                                  frame_id="camera-color-optical")
    candidate = SimpleNamespace(translation=[.1, .2, .4], width_m=.04, score=.12)
    data = SimpleNamespace(info=SimpleNamespace(
        segment=SimpleNamespace(observation=observation), candidates=[candidate],
    ))
    read = AsyncMock(return_value=(data, .5))
    camera = SimpleNamespace(status=AsyncMock(return_value={
        "state": "ready", "reason": "", "observation": {"source": "realsense"},
    }))
    system = SimpleNamespace(
        registry=SimpleNamespace(wait_for=AsyncMock(), transport=Mock(side_effect=lambda x: x)),
        start=AsyncMock(return_value=task),
        status=AsyncMock(return_value={"action_history": [
            {"task_id": "old-task", "skill": "infer_grasps", "action_id": "old-action"},
            {"task_id": "this-task", "skill": "infer_grasps", "action_id": "this-action"},
        ]}),
        clients={"graspnet": SimpleNamespace(status=AsyncMock(return_value=action))},
    )
    closed = Mock()

    @asynccontextmanager
    async def connect(*args, **kwargs):
        try:
            yield system
        finally:
            closed()

    for name, value in {"System": SimpleNamespace(connect=connect), "use_local_token": Mock(),
                        "CameraClient": Mock(return_value=camera), "read_grasps": read}.items():
        monkeypatch.setitem(main.__globals__, name, value)
    return SimpleNamespace(main=main, system=system, completed=completed, action=action,
                           observation=observation, camera=camera, read=read, closed=closed)


@pytest.mark.parametrize("missing,script", [
    ("camera", "01_start_camera.py"), ("yolo", "02_start_locator.py"),
    ("segmentation", "03_start_fastsam.py"), ("graspnet", "04_start_graspnet.py"),
    ("agent", "05_start_agent.py"),
])
async def test_missing_node_names_the_entry_and_does_not_submit(example, missing, script):
    async def wait_for(node, **kwargs):
        if node == missing:
            raise TimeoutError

    example.system.registry.wait_for.side_effect = wait_for
    with pytest.raises(SystemExit, match=script):
        await example.main()
    example.system.start.assert_not_awaited()
    example.read.assert_not_awaited()
    example.closed.assert_called_once()


@pytest.mark.parametrize("state,source", [("disconnected", None), ("ready", "synthetic")])
async def test_requires_fresh_realsense_observation_before_submission(example, state, source):
    example.camera.status.return_value = {"state": state, "reason": "camera_disconnected",
                                         "observation": {"source": source} if source else None}
    with pytest.raises(SystemExit):
        await example.main()
    example.system.start.assert_not_awaited()
    example.read.assert_not_awaited()


async def test_same_task_bindings_and_result_are_used_without_robot_action(example, capsys):
    await example.main()
    locate, segment, propose = example.system.start.await_args.args
    assert [item.skill for item in (locate, segment, propose)] == [
        "locate_object", "segment_region", "infer_grasps",
    ]
    assert locate.args == {"target": "banana", "selection": "only"}
    assert segment.depends_on == [locate.step_id]
    assert segment.inputs["location_id"].step_id == locate.step_id
    assert segment.inputs["location_id"].field == "location_id"
    assert propose.depends_on == [segment.step_id]
    assert propose.inputs["segment_id"].step_id == segment.step_id
    assert propose.inputs["segment_id"].field == "segment_id"
    example.system.clients["graspnet"].status.assert_awaited_once_with("this-action")
    example.read.assert_awaited_once_with("graspnet", "this-candidate", "this-task")
    text = capsys.readouterr().out
    assert "this-frame" in text and "候选数：1" in text and "不执行机械臂动作" in text
    example.closed.assert_called_once()


async def test_failed_task_does_not_read_previous_candidates(example):
    example.completed.state, example.completed.reason = "FAILED", "target_not_found"
    with pytest.raises(SystemExit, match="target_not_found"):
        await example.main()
    example.system.status.assert_not_awaited()
    example.read.assert_not_awaited()


async def test_old_task_history_is_never_used_as_fallback(example):
    example.system.status.return_value["action_history"].pop()
    with pytest.raises(SystemExit, match="本任务唯一"):
        await example.main()
    example.system.clients["graspnet"].status.assert_not_awaited()
    example.read.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("source", "synthetic"),
                                         ("observation_id", "other-frame")])
async def test_changed_observation_is_not_reported_as_a_real_candidate(example, field, value,
                                                                    capsys):
    setattr(example.observation, field, value)
    with pytest.raises(SystemExit, match="观测不一致"):
        await example.main()
    assert "首选位置" not in capsys.readouterr().out
