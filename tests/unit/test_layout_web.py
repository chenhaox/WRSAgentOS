"""Browser edits use only the active disposable scene and reject stale writes."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from wrs_agent import layout_editor
from wrs_agent.env import layout_web as web
from wrs_agent.layout_session import kitchen_session
from wrs_agent.scene import load_scene


@pytest.fixture
def layout_defaults(tmp_path):
    scene = tmp_path / "scene.toml"
    layout = tmp_path / "layout.toml"
    scene.write_text('layout="layout.toml"\n[objects.A]\nsource="configuration"\n'
                     'label="电磁炉"\n[objects.B]\nsource="configuration"\nlabel="锅"\n',
                     encoding="utf-8")
    layout.write_text('[objects.A]\npos=[1,2,3]\nrpy_deg=[0,0,0]\n'
                      '[objects.B]\nparent="A"\npos=[0.2,0,0.1]\nrpy_deg=[0,0,0]\n',
                      encoding="utf-8")
    return scene, layout


@pytest.fixture
def editor(layout_defaults, tmp_path):
    scene, _ = layout_defaults
    directory = tmp_path / "session"
    with kitchen_session(scene, directory) as runtime:
        yield web.LayoutWebEditor(runtime, session_directory=directory, default_scene=scene)


def command(editor, kind="set", **updates):
    state = editor.state()
    data = {"type": kind, "id": "B", "session": state["session"],
            "revision": state["revision"]}
    if kind == "set":
        data.update(pos=[0.3, 0.0, 0.2], rpy_deg=[0.0, 0.0, 90.0])
    data.update(updates)
    return json.dumps(data)


def test_set_preserves_parent_and_never_changes_defaults(editor, layout_defaults):
    scene, layout = layout_defaults
    originals = scene.read_bytes(), layout.read_bytes()
    initial = editor.state()
    reply = editor.handle(command(editor))
    assert reply["type"] == "state"
    assert reply["session"] == initial["session"]
    assert reply["revision"] != initial["revision"]
    child = next(obj for obj in reply["objects"] if obj["id"] == "B")
    assert child["parent"] == "A"
    assert child["pos"] == [0.3, 0.0, 0.2]
    assert child["world_pos"] == pytest.approx([1.3, 2.0, 3.2])
    assert (scene.read_bytes(), layout.read_bytes()) == originals
    assert editor.handle('{"type":"get"}') == reply


@pytest.mark.parametrize("updates", [
    {"pos": [0.0, 0.0]}, {"pos": [0.0, 0.0, 0.0, 0.0]},
    {"pos": ["1", 0.0, 0.0]}, {"pos": [True, 0.0, 0.0]},
    {"pos": [float("nan"), 0.0, 0.0]}, {"rpy_deg": [0.0, float("inf"), 0.0]},
    {"parent": "A"}, {"path": "../layout.toml"}, {"id": "missing"},
    {"session": "old-session"}, {"revision": "0" * 64}, {"type": "unknown"},
])
def test_invalid_commands_do_not_write(editor, updates):
    before = editor.path.read_bytes()
    result = editor.handle(command(editor, **updates))
    assert result["type"] == "error"
    assert result["state"]["type"] == "state"
    assert editor.path.read_bytes() == before


@pytest.mark.parametrize("message", ['{"type":"get","extra":1}', '{', " " * 4097,
                                       b'{"type":"get"}'])
def test_bad_message_is_rejected(editor, message):
    before = editor.path.read_bytes()
    assert editor.handle(message)["type"] == "error"
    assert editor.path.read_bytes() == before


def test_cli_edit_invalidates_revision(editor):
    request = command(editor)
    layout_editor.save_pose(editor.scene, "A", [2.0, 3.0, 4.0], [0.0] * 3,
                            session_directory=editor.directory)
    before = editor.path.read_bytes()
    reply = editor.handle(request)
    assert reply["type"] == "error"
    assert reply["state"]["revision"] == editor.state()["revision"]
    assert editor.path.read_bytes() == before


def test_cli_race_between_revision_check_and_save_is_rejected(editor, monkeypatch):
    save = web.save_pose
    external = editor.path.read_bytes() + b"# concurrent CLI edit\n"

    def concurrent(*args, **kwargs):
        editor.path.write_bytes(external)
        return save(*args, **kwargs)

    monkeypatch.setattr(web, "save_pose", concurrent)
    assert editor.handle(command(editor))["type"] == "error"
    assert editor.path.read_bytes() == external


def test_parallel_commands_cannot_overwrite_each_other(editor):
    request = command(editor)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(editor.handle, [request, request]))
    assert sorted(result["type"] for result in results) == ["error", "state"]


def test_reset_requires_current_revision_and_restores_only_selected_pose(editor):
    stale_reset = command(editor, "reset")
    assert editor.handle(command(editor))["type"] == "state"
    assert editor.handle(stale_reset)["type"] == "error"
    result = editor.handle(command(editor, "reset"))
    assert result["type"] == "state"
    child = next(obj for obj in result["objects"] if obj["id"] == "B")
    assert child["pos"] == [0.2, 0.0, 0.1]
    assert child["rpy_deg"] == [0.0, 0.0, 0.0]
    assert child["parent"] == "A"


def test_old_editor_never_follows_restarted_05(layout_defaults, tmp_path):
    default, layout = layout_defaults
    directory = tmp_path / "session"
    with kitchen_session(default, directory) as runtime:
        editor = web.LayoutWebEditor(runtime, session_directory=directory, default_scene=default)
        request = command(editor)
    with kitchen_session(default, directory) as restarted:
        before = (restarted.parent / layout.name).read_bytes()
        result = editor.handle(request)
        assert result["type"] == "error"
        assert "state" not in result
        assert (restarted.parent / layout.name).read_bytes() == before
        assert load_scene(restarted) == load_scene(default)


@pytest.mark.parametrize("origin,host,allowed", [
    ("http://127.0.0.1:8000", "127.0.0.1:8000", True),
    ("http://localhost:8000", "localhost:8000", True),
    (None, "127.0.0.1:8000", False),
    ("null", "127.0.0.1:8000", False),
    ("http://evil.example", "127.0.0.1:8000", False),
    ("http://127.0.0.1:8001", "127.0.0.1:8000", False),
    ("http://127.0.0.1:8000", "evil.example:8000", False),
])
def test_only_loopback_same_origin_editor_is_allowed(origin, host, allowed):
    assert web.same_origin({"Origin": origin, "Host": host}, 8000) is allowed


def test_duplicate_origin_is_rejected():
    from websockets.datastructures import Headers

    headers = Headers([("Origin", "http://127.0.0.1:8000"),
                       ("Origin", "http://evil.example"), ("Host", "127.0.0.1:8000")])
    assert not web.same_origin(headers, 8000)


def test_page_injects_assets_and_serves_only_known_names(tmp_path, monkeypatch):
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    monkeypatch.setattr(web, "ASSETS", tmp_path)
    (tmp_path / "editor.js").write_text("// editor", encoding="utf-8")
    (tmp_path / "editor.css").write_text("/* editor */", encoding="utf-8")

    def static_response(path):
        if path in {"/", "/index.html"}:
            body = b"<html><head></head><body><canvas></canvas></body></html>"
            return Response(200, "OK", Headers({"Content-Length": str(len(body))}), body)
        return Response(404, "Not Found", Headers({"Content-Length": "0"}), b"")

    response = web._http_response("/", static_response)
    assert b"/kitchen-editor/editor.js" in response.body
    assert b"<canvas>" in response.body
    assert int(response.headers["Content-Length"]) == len(response.body)
    assert web._http_response("/kitchen-editor/editor.js", static_response).body == b"// editor"
    assert web._http_response("/kitchen-editor/../editor.js", static_response).status_code == 404
