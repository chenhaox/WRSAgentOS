import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest
from conftest import action, eventually

from wrs_agent.drivers.cameras.synthetic import SyntheticCamera
from wrs_agent.errors import AgentError
from wrs_agent.executor import ActionExecutor
from wrs_agent.nodes.camera.frames import Observation
from wrs_agent.nodes.grasp.data import GraspData, GraspSet, LatestGrasps, pack_grasps, unpack_grasps
from wrs_agent.nodes.grasp.graspnet import candidates_from_rows, sample_target
from wrs_agent.nodes.grasp.node import GraspNetNode
from wrs_agent.nodes.segmentation.data import SegmentData, SegmentInfo, pack_segment, unpack_segment
from wrs_agent.nodes.segmentation.node import SegmentationState
from wrs_agent.nodes.segmentation.sam3 import Sam3Segmenter, select_mask
from wrs_agent.nodes.vision.artifacts import ArtifactCache, ArtifactOwner, ArtifactRequest
from wrs_agent.schemas import ActionState
from wrs_agent.skills.vision.grasping import SEGMENT, SegmentOutput


@pytest.fixture
def segment():
    camera = SyntheticCamera(width=32, height=24)
    camera.open()
    frame = camera.read()
    info = Observation(observation_id="obs.1", stream_id="stream", sequence=1,
                       source="synthetic", frame_id=frame.frame_id, width=32, height=24,
                       captured_at_ns=frame.captured_at_ns, calibration=frame.calibration)
    owner = ArtifactOwner(node_id="sam3", boot_id="boot", control_epoch=0,
                          task_id="task", action_id="action")
    mask = frame.depth < 300
    meta = SegmentInfo(segment_id="seg", owner=owner, observation=info, target="box",
                       model="fixture", weights_sha256="0" * 64, score=0.9,
                       pixel_count=int(mask.sum()))
    return SegmentData(meta, {key: getattr(frame, key) for key in ("rgb", "depth", "points")},
                       mask)


def rows():
    row = np.array([0.9, 0.04, 0.02, 0.03, *np.eye(3).reshape(-1), 0., 0., 0.25, -1.])
    return row[None]


@pytest.mark.parametrize("clip_sha", [None, "1" * 64])
def test_segment_packet_retains_scene_and_same_pixel_grid(segment, clip_sha):
    if clip_sha:
        segment = SegmentData(segment.info.model_copy(update={
            "text_weights_sha256": clip_sha, "score_kind": "clip_cosine",
        }), segment.arrays, segment.mask)
    restored, age = unpack_segment(pack_segment(segment, 0.5))
    assert age == 0.5 and restored.info == segment.info
    np.testing.assert_array_equal(restored.mask, segment.mask)
    for key in segment.arrays:
        np.testing.assert_array_equal(restored.arrays[key], segment.arrays[key])
        assert not restored.arrays[key].flags.writeable
    assert restored.arrays["points"][~restored.mask].shape[0] > 0
    assert not restored.mask.flags.writeable


def test_clip_score_requires_text_weight_provenance(segment):
    payload = segment.info.model_dump()
    payload["score_kind"] = "clip_cosine"
    with pytest.raises(ValueError, match="score_kind_mismatch"):
        SegmentInfo.model_validate(payload)
    payload["score_kind"] = "sam3_presence_class"
    payload["text_weights_sha256"] = "1" * 64
    with pytest.raises(ValueError, match="score_kind_mismatch"):
        SegmentInfo.model_validate(payload)


@pytest.mark.parametrize("change", [lambda p: p[:-1], lambda p: p + b"x", lambda p: p[:12]])
def test_segment_packet_rejects_malformed_mask_or_truncation(segment, change):
    with pytest.raises(ValueError):
        unpack_segment(change(pack_segment(segment, 0)))


def test_artifact_cache_is_bounded_and_rejects_other_tasks_stop_and_restart(segment):
    cache = ArtifactCache(capacity=2, max_bytes=100, ttl=1)
    owner = segment.info.owner
    executor = SimpleNamespace(boot_id="boot", epoch=0,
                               status=lambda _: SimpleNamespace(state=ActionState.SUCCEEDED))
    for key in ("a", "b", "c"):
        cache.put(key, owner, segment, nbytes=40, observed_at=time.monotonic())
    assert cache.nbytes == 80 and list(cache.entries) == ["b", "c"]
    with pytest.raises(AgentError, match="artifact_expired"):
        cache.get(ArtifactRequest(artifact_id="a", task_id="task"), executor)
    with pytest.raises(AgentError, match="task_mismatch"):
        cache.get(ArtifactRequest(artifact_id="c", task_id="other"), executor)
    request = ArtifactRequest(artifact_id="c", task_id="task")
    assert cache.get(request, executor)[0] is segment
    executor.epoch = 1
    with pytest.raises(AgentError, match="invalidated"):
        cache.get(request, executor)
    executor.epoch, executor.boot_id = 0, "restarted"
    with pytest.raises(AgentError, match="invalidated"):
        cache.get(request, executor)
    with pytest.raises(AgentError, match="stale"):
        cache.put("old", owner, segment, nbytes=40, observed_at=time.monotonic() - 2)


def test_artifact_rejects_unfinished_and_failed_actions(segment):
    cache = ArtifactCache()
    cache.put("seg", segment.info.owner, segment, nbytes=segment.nbytes,
              observed_at=time.monotonic())
    for state in (ActionState.RUNNING, ActionState.FAILED, ActionState.CANCELLED):
        executor = SimpleNamespace(boot_id="boot", epoch=0,
                                   status=lambda _, state=state: SimpleNamespace(state=state))
        with pytest.raises(AgentError, match="invalidated"):
            cache.get(ArtifactRequest(artifact_id="seg", task_id="task"), executor)


def test_target_ambiguity_and_explicit_spatial_selection():
    boxes, scores = np.array([[10, 10, 20, 30], [40, 10, 50, 30]]), np.array([.9, .8])
    with pytest.raises(ValueError, match="target_ambiguous"):
        select_mask(boxes, scores, "only")
    assert select_mask(boxes, scores, "leftmost") == 0
    assert select_mask(boxes, scores, "rightmost") == 1
    with pytest.raises(ValueError, match="target_not_found"):
        select_mask(np.empty((0, 4)), np.array([]), "only")


def test_graspnet_samples_only_finite_target_xyz_and_checks_gripper_width(segment):
    cloud = sample_target(segment.arrays["points"], segment.mask, count=1000)
    assert cloud.shape == (1000, 3) and cloud.dtype == np.float32
    np.testing.assert_allclose(cloud[:, 2], .25)
    with pytest.raises(ValueError, match="insufficient_target_depth"):
        sample_target(segment.arrays["points"], np.zeros_like(segment.mask))
    assert candidates_from_rows(rows(), max_width_m=.05)[0].width_m == .04
    with pytest.raises(ValueError, match="no_grasp"):
        candidates_from_rows(rows(), max_width_m=.03)
    invalid = rows()
    invalid[0, 4] = 2
    with pytest.raises(ValueError, match="rotation"):
        candidates_from_rows(invalid, max_width_m=.05)


def test_grasp_packet_preserves_provenance_and_whole_scene(segment):
    info = GraspSet(candidate_set_id="grasps", owner=segment.info.owner,
                    segment=segment.info, model="fixture", weights_sha256="0" * 64,
                    source_revision="0" * 40,
                    candidates=candidates_from_rows(rows(), max_width_m=.05))
    data, age = unpack_grasps(pack_grasps(GraspData(info, segment), 1.5))
    assert age == 1.5 and data.info == info and not data.info.collision_checked
    np.testing.assert_array_equal(data.segment.arrays["points"], segment.arrays["points"])


@pytest.fixture
def grasp_data(segment):
    info = GraspSet(candidate_set_id="grasps", owner=segment.info.owner,
                    segment=segment.info, model="fixture", weights_sha256="0" * 64,
                    source_revision="0" * 40,
                    candidates=candidates_from_rows(rows(), max_width_m=.05))
    return GraspData(info, segment)


async def test_latest_grasp_is_only_a_valid_successful_unexpired_reference(grasp_data):
    node = object.__new__(GraspNetNode)
    node.latest_request = None
    with pytest.raises(AgentError, match="grasp_not_available"):
        await node.latest({})
    state = SimpleNamespace(state=ActionState.RUNNING)
    node.executor = SimpleNamespace(boot_id="boot", epoch=0, status=lambda _: state)
    node.cache = ArtifactCache()
    node._check_segment = AsyncMock()
    node.cache.put("grasps", grasp_data.info.owner, grasp_data, nbytes=1,
                   observed_at=time.monotonic() - 6)
    node.latest_request = ArtifactRequest(artifact_id="grasps", task_id="task")
    with pytest.raises(AgentError, match="invalidated"):
        await node.latest({})
    state.state = ActionState.SUCCEEDED
    ref = LatestGrasps.model_validate(await node.latest({}))
    assert ref.candidate_set_id == "grasps" and ref.observation_id == "obs.1"
    assert ref.task_id == "task" and ref.age_s >= 6
    node._check_segment.assert_awaited_once_with(grasp_data.segment.info)

    async def cancel_during_check(_):
        node.executor.epoch += 1

    node._check_segment.side_effect = cancel_during_check
    with pytest.raises(AgentError, match="invalidated"):
        await node.latest({})
    node.cache.ttl = 1
    with pytest.raises(AgentError, match="expired"):
        await node.latest({})


async def test_grasp_preview_uses_inference_frame_once_then_checks_validity(
    grasp_data, monkeypatch,
):
    from wrs_agent.nodes.camera import preview

    ui = preview.CameraPreview(endpoint="unused", env_id="test")
    ref = LatestGrasps(candidate_set_id="grasps", task_id="task", observation_id="obs.1", age_s=6)
    ui.grasp_bus = SimpleNamespace(request=AsyncMock(return_value=ref.model_dump()))
    ui.client = SimpleNamespace(read=AsyncMock(), status=AsyncMock())
    read = AsyncMock(return_value=(grasp_data, 6.0))
    monkeypatch.setattr(preview, "read_grasps", read)
    await ui._update("grasps")
    result = ui.latest["grasps"]
    assert result["state"] == "ready" and result["view"] == "grasps"
    assert result["observation_id"] == "obs.1" and result["task_id"] == "task"
    assert result["grasps"][0]["translation"] == [0, 0, .25]
    assert len(result["points"]) == 32 * 24
    assert time.monotonic() - ui.observed_at["grasps"] >= 6
    await ui._update("grasps")
    read.assert_awaited_once_with(ui.grasp_bus, "grasps", "task")
    assert ui.grasp_bus.request.await_count == 2
    ui.client.read.assert_not_awaited()
    ui.grasp_bus.request.side_effect = AgentError("artifact_invalidated")
    await ui._update("grasps")
    assert ui.latest["grasps"] == {"state": "unavailable", "reason": "artifact_invalidated"}
    ui.client.status.assert_not_awaited()


async def test_grasp_preview_rejects_mismatched_observation(grasp_data, monkeypatch):
    from wrs_agent.nodes.camera import preview

    ui = preview.CameraPreview(endpoint="unused", env_id="test")
    ui.grasp_bus = SimpleNamespace(request=AsyncMock(return_value=dict(
        candidate_set_id="grasps", task_id="task", observation_id="wrong.1", age_s=0,
    )))
    monkeypatch.setattr(preview, "read_grasps", AsyncMock(return_value=(grasp_data, 0)))
    await ui._update("grasps")
    assert ui.latest["grasps"]["state"] != "ready"
    assert "points" not in ui.latest["grasps"]


def test_sam_checks_local_weights_before_sdk_import(tmp_path):
    with pytest.raises(FileNotFoundError):
        Sam3Segmenter(tmp_path / "missing.pt", "0" * 64)
    file = tmp_path / "model.pt"
    file.write_bytes(b"not-weights")
    with pytest.raises(ValueError, match="checksum"):
        Sam3Segmenter(file, "0" * 64)


async def test_artifact_identity_comes_from_accepted_action(tmp_path):
    accepted = []

    async def handler(*_):
        accepted.append(executor.active_request())
        return SegmentOutput(segment_id="seg", observation_id="obs")

    executor = ActionExecutor(tmp_path / "sam.sqlite3", state=SegmentationState(),
                              skills=(SEGMENT.bind(handler),), backend="fixture",
                              features_extra={"robot_controls": False})
    try:
        with pytest.raises(AgentError, match="no_active"):
            executor.active_request()
        request = action(executor, "segment_object", {"target": "box"}, task_id="real-task")
        assert (await executor.submit(request)).accepted
        await eventually(lambda: executor.status(request.action_id),
                         lambda status: status.state == ActionState.SUCCEEDED)
        assert accepted[0].task_id == "real-task"
        accepted[0].args["target"] = "different"
        assert executor.records[request.action_id][0]["args"]["target"] == "box"
    finally:
        await executor.close()
