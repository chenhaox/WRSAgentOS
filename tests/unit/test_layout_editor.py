"""Interactive edits preserve the TOML template and never partially overwrite it."""

import tomllib

import pytest

from wrs_agent import layout_editor as editor
from wrs_agent.layout_session import current_session, kitchen_session
from wrs_agent.scene import load_scene


@pytest.fixture
def layout_files(tmp_path):
    scene = tmp_path / "scene.toml"
    scene.write_text('layout = "layout.toml"\n' + "\n".join(
        f'[objects.{name}]\nsource="configuration"\nlabel="{label}"'
        for name, label in (("A", "电磁炉"), ("B", "大锅"))
    ), encoding="utf-8")
    layout = tmp_path / "layout.toml"
    layout.write_bytes(('''# 保留使用说明\r
[objects.A]\r
pos = [1, 2, 3] # 保留行尾注释\r
rpy_deg = [0, 0, 0]\r
\r
# 大锅跟随电磁炉\r
[objects.B]\r
parent = "A"\r
pos = [0.2, 0, 0.1]\r
rpy_deg = [0, 0, 0]\r
''').encode())
    return scene, layout


@pytest.fixture
def runtime_layout(layout_files, tmp_path):
    directory = tmp_path / "session"
    with kitchen_session(layout_files[0], directory) as scene:
        yield scene, scene.parent / "layout.toml", directory


@pytest.mark.parametrize("value", ["1, 2, 3", "1，2，3", "1 2 3"])
def test_parse_vector_accepts_common_separators(value):
    assert editor.parse_vector(value) == [1, 2, 3]


@pytest.mark.parametrize("value", ["", "1,2", "1,2,3,4", "nan,0,0", "0,inf,0", "x,y,z"])
def test_parse_vector_rejects_bad_values(value):
    with pytest.raises(ValueError):
        editor.parse_vector(value)


def test_selection_by_name_id_number_and_ambiguous_label(layout_files):
    objects = load_scene(layout_files[0])
    for value in ("2", "大锅", "b"):
        assert editor.select_object(value, objects) == "B"
    objects["A"] = objects["A"].model_copy(update={"label": "大锅"})
    for value in ("大锅", "999", "锅"):
        with pytest.raises(ValueError):
            editor.select_object(value, objects)


def test_save_preserves_comments_parent_and_crlf_and_moves_child(layout_files):
    scene, layout = layout_files
    original_child = layout.read_bytes().split(b"[objects.B]")[1]
    editor.save_pose(scene, "A", [2.0, 3.0, 4.0], [0.0, 0.0, 90.0])
    content = layout.read_bytes()
    assert content.split(b"[objects.B]")[1] == original_child
    assert "# 保留行尾注释".encode() in content
    assert b"\n" not in content.replace(b"\r\n", b"")
    assert load_scene(scene)["B"].pos == pytest.approx([2, 3.2, 4.1])


def test_save_refuses_invalid_and_failed_replace_without_losing_original(layout_files, monkeypatch):
    scene, layout = layout_files
    original = layout.read_bytes()
    with pytest.raises(ValueError):
        editor.save_pose(scene, "B", [float("nan"), 0.0, 0.0], [0.0] * 3)
    assert layout.read_bytes() == original

    def denied(*args):
        raise PermissionError("file locked")

    monkeypatch.setattr(editor.os, "replace", denied)
    with pytest.raises(PermissionError):
        editor.save_pose(scene, "B", [0.0] * 3, [0.0] * 3)
    assert layout.read_bytes() == original
    assert not list(layout.parent.glob(".layout-*.tmp"))


def test_concurrent_external_edit_is_not_overwritten(layout_files, monkeypatch):
    scene, layout = layout_files
    external = layout.read_bytes() + b"# external edit\r\n"
    apply = editor.apply_layout

    def concurrent(*args):
        result = apply(*args)
        layout.write_bytes(external)
        return result

    monkeypatch.setattr(editor, "apply_layout", concurrent)
    with pytest.raises(ValueError, match="其他程序"):
        editor.save_pose(scene, "A", [0.0] * 3, [0.0] * 3)
    assert layout.read_bytes() == external


def test_editor_keeps_angle_when_blank_and_reprompts(runtime_layout, monkeypatch, capsys):
    scene, layout, directory = runtime_layout
    answers = iter(["电磁炉", "bad", "2，3，4", "", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert editor.main(["--session-dir", str(directory)]) == 0
    saved = tomllib.loads(layout.read_text(encoding="utf-8"))
    assert saved["objects"]["A"]["pos"] == [2, 3, 4]
    assert saved["objects"]["A"]["rpy_deg"] == [0, 0, 0]
    assert "已应用到本次场景" in capsys.readouterr().out


def test_cancel_and_list_do_not_write(runtime_layout, monkeypatch):
    scene, layout, directory = runtime_layout
    original = layout.read_bytes()
    assert editor.main(["--session-dir", str(directory), "--list"]) == 0
    answers = iter(["B", "0 0 0", "q", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert editor.main(["--session-dir", str(directory)]) == 0
    assert layout.read_bytes() == original


def test_restarting_session_resets_all_poses_without_changing_defaults(layout_files, tmp_path):
    default_scene, default_layout = layout_files
    original_scene, original_layout = default_scene.read_bytes(), default_layout.read_bytes()
    directory = tmp_path / "session"
    expected = load_scene(default_scene)
    with kitchen_session(default_scene, directory) as first:
        assert current_session(directory) == first
        editor.save_pose(first, "A", [9.0] * 3, [0.0, 0.0, 90.0], session_directory=directory)
        assert load_scene(first)["A"].pos == [9, 9, 9]
        assert default_scene.read_bytes() == original_scene
        assert default_layout.read_bytes() == original_layout
    assert not first.exists()
    with kitchen_session(default_scene, directory) as second:
        assert second != first and load_scene(second) == expected
        with pytest.raises(ValueError, match="重新启动"):
            editor.save_pose(first, "A", [8.0] * 3, [0.0] * 3, session_directory=directory)
        assert load_scene(second) == expected
    assert default_scene.read_bytes() == original_scene
    assert default_layout.read_bytes() == original_layout
    assert not (directory / "current.json").exists()


def test_duplicate_start_does_not_reset_running_scene(layout_files, tmp_path):
    directory = tmp_path / "session"
    with kitchen_session(layout_files[0], directory) as first:
        editor.save_pose(first, "A", [9.0] * 3, [0.0] * 3)
        with pytest.raises(RuntimeError, match="node_already_running"):
            with kitchen_session(layout_files[0], directory):
                pass
        assert current_session(directory) == first
        assert load_scene(first)["A"].pos == [9, 9, 9]


def test_stale_session_cannot_be_edited_and_is_not_reused(layout_files, tmp_path, capsys):
    directory = tmp_path / "session"
    directory.mkdir()
    pointer = directory / "current.json"
    pointer.write_text('{"scene":"stale/scene.toml"}', encoding="utf-8")
    assert editor.main(["--session-dir", str(directory), "--list"]) == 1
    assert "先启动 05" in capsys.readouterr().out
    with kitchen_session(layout_files[0], directory) as runtime:
        assert runtime.parent.name.startswith("run-")
        assert current_session(directory) == runtime


def test_editor_cannot_write_defaults_even_if_passed_to_save(runtime_layout, layout_files):
    _, _, directory = runtime_layout
    scene, layout = layout_files
    original = layout.read_bytes()
    with pytest.raises(ValueError):
        editor.save_pose(scene, "A", [9.0] * 3, [0.0] * 3, session_directory=directory)
    assert layout.read_bytes() == original


def test_multiline_position_and_default_angles_are_editable():
    text = "[objects.A]\npos = [\n 0,\n 0,\n 0\n] # pos\n"
    result = editor.replace_pose(text, "A", [1.0, 2.0, 3.0], [0.0] * 3)
    assert "# pos" in result
    assert tomllib.loads(result)["objects"]["A"] == {
        "pos": [1.0, 2.0, 3.0], "rpy_deg": [0.0] * 3,
    }
