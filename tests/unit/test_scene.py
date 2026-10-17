import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from wrs_agent import System, sync
from wrs_agent.env.wrs import VirtualState
from wrs_agent.processes import ROOT, LocalStack
from wrs_agent.scene import load_scene
from wrs_agent.schemas import MAX_BYTES, ObjectData, RobotData, SceneData, encode

IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def object_fields():
    return dict(source="configuration", pos=[0.1, 0.2, 0.3], rotmat=copy.deepcopy(IDENTITY),
                geometry={"kind": "box", "xyz_lengths": [0.1, 0.2, 0.3]})


@pytest.mark.parametrize("change", [
    {"pos": [0.0, float("nan"), 0.0]},
    {"rotmat": [[1.0, 0.0, 0.0]] * 3},
    {"rotmat": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]]},
    {"rotmat": [[1.0, 0.0, 0.0]]},
    {"geometry": {"kind": "box", "xyz_lengths": [0.0, 0.1, 0.1]}},
    {"geometry": {"kind": "box", "xyz_lengths": [0.1, 0.1]}},
    {"geometry": {"kind": "mesh", "path": "arbitrary.stl"}},
    {"rgb": [0.0, 1.1, 0.0]},
    {"observed_at_ns": 0},
])
def test_invalid_geometry_is_rejected(change):
    with pytest.raises(ValidationError):
        ObjectData.model_validate({**object_fields(), **change})


def test_unknown_pose_is_not_filled_with_identity():
    obj = ObjectData(source="mock", location="table")
    assert obj.pos is None and obj.rotmat is None and obj.geometry is None
    scene = SceneData(robot=RobotData(), objects={"A": obj})
    assert SceneData.model_validate_json(scene.model_dump_json()) == scene
    assert scene.frame_id == "world" and scene.position_unit == "m"
    assert "scene_revision" not in scene.model_dump()


@pytest.mark.parametrize("change", [
    {"frame_id": "camera"},
    {"objects": {str(i): ObjectData(source="mock") for i in range(33)}},
])
def test_scene_rejects_unknown_frame_or_unbounded_objects(change):
    with pytest.raises(ValidationError):
        SceneData(robot=RobotData(), **change)


def test_nested_wrs_snapshot_cannot_mutate_backend_state():
    kin = dict(qs=[0.0] * 6, tcp_name="flange", tcp_pos=[0.0] * 3,
               tcp_rotmat=copy.deepcopy(IDENTITY), observed_at_ns=1)
    obj = ObjectData(**object_fields())
    state = VirtualState(kin, {"A": obj})
    first = state.snapshot()
    first.robot.kinematics.qs[0] = 42.0
    first.robot.kinematics.tcp_rotmat[0][0] = 0.0
    first.objects["A"].pos[0] = 42.0
    first.objects["A"].geometry.xyz_lengths[0] = 42.0
    first.objects["A"].rotmat[0][0] = 0.0
    second = state.snapshot()
    assert second.objects["A"] == obj
    assert second.robot.kinematics.qs == [0.0] * 6
    assert second.robot.kinematics.tcp_rotmat == IDENTITY
    assert state.version == 0


def test_shipped_scene_fits_message_budget_and_keeps_configuration_provenance():
    objects = load_scene(ROOT / "examples/wrs/scene.toml")
    assert set(objects) == {"table", "A", "plate"}
    assert all(obj.source == "configuration" and obj.observed_at_ns is None
               for obj in objects.values())
    assert len(encode(SceneData(robot=RobotData(), objects=objects))) < MAX_BYTES // 2


@pytest.mark.parametrize("content", [
    'frame_id = "camera"',
    '[objects.A]\nsource = "vision"',
    '[objects.A]\nsource = "configuration"\nobserved_at_ns = 1',
    '# ' + 'a' * MAX_BYTES,
], ids=["wrong-frame", "sensor-source", "sensor-time", "oversized"])
def test_bad_configuration_fails_before_spawning(tmp_path, content):
    path = tmp_path / "scene.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        LocalStack(backend="wrs", scene=path)


def test_scene_parameter_is_explicit_and_only_sent_to_wrs(tmp_path, monkeypatch):
    path = ROOT / "examples/wrs/scene.toml"
    with pytest.raises(ValueError, match="scene_requires_wrs_backend"):
        LocalStack(scene=path)
    with pytest.raises(FileNotFoundError):
        LocalStack(backend="wrs", scene=tmp_path / "missing.toml")
    with pytest.raises(ValueError, match="scene_requires_wrs_node"):
        LocalStack(backend="wrs", scene=path, bindings=ROOT / "configs/tts.toml")
    monkeypatch.chdir(tmp_path)
    stack = LocalStack(backend="wrs", scene=path)
    stack.endpoint = "tcp/127.0.0.1:12345"
    command = stack.node_command("wrs")
    assert json.loads(command[command.index("--options") + 1])["scene"] == str(path.resolve())
    agent = stack.node_command("agent")
    assert "scene" not in json.loads(agent[agent.index("--options") + 1])


def test_sync_launch_forwards_scene_without_starting_hardware(monkeypatch):
    captured = {}

    @asynccontextmanager
    async def launch(**kwargs):
        captured.update(kwargs)
        yield SimpleNamespace()

    monkeypatch.setattr(System, "launch", launch)
    with sync.launch(backend="wrs", scene="example.toml"):
        pass
    assert captured["scene"] == "example.toml"
