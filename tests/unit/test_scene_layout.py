"""Layout parsing and parent transforms; no WRS, viewer or hardware required."""

from pathlib import Path

import pytest

from wrs_agent.scene import load_scene
from wrs_agent.scene_layout import rotation_from_degrees

ROOT = Path(__file__).resolve().parents[2]


def scene_file(tmp_path, layout, names=("A", "B")):
    scene = tmp_path / "scene.toml"
    scene.write_text('layout = "layout.toml"\n' + "\n".join(
        f'[objects.{name}]\nsource = "configuration"' for name in names
    ), encoding="utf-8")
    (tmp_path / "layout.toml").write_text(layout, encoding="utf-8")
    return scene


def test_child_follows_parent_translation_and_rotation_regardless_of_file_order(tmp_path):
    path = scene_file(tmp_path, '''
[objects.B]
parent = "A"
pos = [0.2, 0, 0.1]
rpy_deg = [90, 0, 0]
[objects.A]
pos = [1, 2, 3]
rpy_deg = [0, 0, 90]
''')
    objects = load_scene(path)
    assert objects["B"].pos == pytest.approx([1, 2.2, 3.1])
    for actual, expected in zip(objects["B"].rotmat,
                                [[0, 0, 1], [1, 0, 0], [0, 1, 0]], strict=True):
        assert actual == pytest.approx(expected, abs=1e-12)


def test_rotation_order_is_fixed_axis_xyz():
    expected = [[0, 0, 1], [1, 0, 0], [0, 1, 0]]
    for actual, row in zip(rotation_from_degrees([90, 0, 90]), expected, strict=True):
        assert actual == pytest.approx(row, abs=1e-12)


@pytest.mark.parametrize("layout, message", [
    ('[objects.A]\npos=[0,0,0]', "layout_objects_mismatch"),
    ('[objects.A]\npos=[0,0,0]\n[objects.B]\npos=[0,0,0]\n'
     '[objects.typo]\npos=[0,0,0]', "layout_objects_mismatch"),
    ('[objects.A]\nparent="missing"\npos=[0,0,0]\n'
     '[objects.B]\npos=[0,0,0]', "layout_unknown_parent"),
    ('[objects.A]\nparent="B"\npos=[0,0,0]\n'
     '[objects.B]\nparent="A"\npos=[0,0,0]', "layout_parent_cycle"),
    ('[objects.A]\nparent="A"\npos=[0,0,0]\n'
     '[objects.B]\npos=[0,0,0]', "layout_parent_cycle"),
    ('[objects.A]\npos=[0,0]\n[objects.B]\npos=[0,0,0]', "pos"),
    ('[objects.A]\npos=[nan,0,0]\n[objects.B]\npos=[0,0,0]', "finite"),
    ('[objects.A]\npos=[0,0,0]\nrpy_deg=[0,0,inf]\n'
     '[objects.B]\npos=[0,0,0]', "finite"),
    ('[objects.A]\npos=[0,0,0]\nyaw=90\n'
     '[objects.B]\npos=[0,0,0]', "yaw"),
])
def test_invalid_layout_is_rejected(tmp_path, layout, message):
    with pytest.raises(ValueError, match=message):
        load_scene(scene_file(tmp_path, layout))


def test_layout_path_cannot_escape_scene_directory(tmp_path):
    path = tmp_path / "scene.toml"
    path.write_text('layout = "../outside.toml"', encoding="utf-8")
    with pytest.raises(ValueError):
        load_scene(path)


def test_missing_or_oversized_layout_is_rejected(tmp_path):
    path = scene_file(tmp_path, "# " + "a" * 65536, names=())
    with pytest.raises(ValueError, match="layout_config_too_large"):
        load_scene(path)
    (tmp_path / "layout.toml").unlink()
    with pytest.raises(FileNotFoundError):
        load_scene(path)


def test_legacy_scene_still_loads_without_layout():
    objects = load_scene(ROOT / "examples/wrs/scene.toml")
    assert set(objects) == {"table", "A"}


def test_loading_customizable_defaults_is_read_only_and_keeps_materials():
    directory = ROOT / "wrs_agent/assets/kitchen"
    before = {name: (directory / name).read_bytes() for name in ("scene.toml", "layout.toml")}
    objects = load_scene(directory / "scene.toml")
    assert len(objects) == 17
    assert objects["measuring_cup"].alpha == 0.28
    assert load_scene(directory / "scene.toml") == objects
    assert all((directory / name).read_bytes() == contents for name, contents in before.items())
