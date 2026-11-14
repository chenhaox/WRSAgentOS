"""Live layout changes update authoritative state and keep old state on invalid input."""

import asyncio
from types import SimpleNamespace

import pytest
from conftest import action

from wrs_agent.env import wrs as adapter
from wrs_agent.layout_editor import save_pose
from wrs_agent.scene import load_scene
from wrs_agent.scene_reload import SceneReloader


@pytest.fixture
def reloader(tmp_path):
    path = tmp_path / "scene.toml"
    path.write_text('layout="layout.toml"\n[objects.A]\nsource="configuration"', encoding="utf-8")
    (tmp_path / "layout.toml").write_text('[objects.A]\npos=[0,0,0]', encoding="utf-8")
    objects = load_scene(path)
    model = SimpleNamespace(load_scene=lambda objects: objects)
    executor = SimpleNamespace(
        active=None, _closed=False, state_refreshing=False, leases={"old": 1},
        world=SimpleNamespace(objects=objects, version=0),
    )

    async def call(fn, *args):
        assert executor.state_refreshing
        return fn(*args)

    return SceneReloader(executor, call, model, path, objects)


async def test_idle_reload_updates_state_invalidates_leases_and_is_idempotent(reloader):
    save_pose(reloader.path, "A", [1.0, 2.0, 3.0], [0.0, 0.0, 90.0])
    assert await reloader.reload_once()
    env = reloader.executor
    assert env.world.objects["A"].pos == [1, 2, 3]
    assert env.world.version == 1 and not env.leases and not env.state_refreshing
    assert not await reloader.reload_once()
    assert env.world.version == 1


async def test_active_and_newly_accepted_actions_defer_reload(reloader, monkeypatch):
    save_pose(reloader.path, "A", [1.0] * 3, [0.0] * 3)
    reloader.executor.active = "action"
    assert not await reloader.reload_once()
    assert reloader.executor.world.version == 0
    reloader.executor.active = None
    # Simulate an action entering during asyncio.to_thread's file read.
    def read(path):
        reloader.executor.active = "new action"
        return load_scene(path)

    with monkeypatch.context() as patch:
        patch.setattr("wrs_agent.scene_reload.load_scene", read)
        assert not await reloader.reload_once()
    reloader.executor.active = None
    assert await reloader.reload_once()


async def test_invalid_file_keeps_previous_and_recovers_after_fix(reloader, caplog):
    layout = reloader.path.with_name("layout.toml")
    layout.write_text("not valid toml", encoding="utf-8")
    assert not await reloader.reload_once()
    assert not await reloader.reload_once()
    assert len(caplog.records) == 1
    assert reloader.executor.world.objects["A"].pos == [0, 0, 0]
    layout.write_text('[objects.A]\npos=[1,2,3]', encoding="utf-8")
    assert await reloader.reload_once()
    assert reloader.last_error is None


async def test_geometry_or_material_changes_require_restart(reloader):
    with reloader.path.open("a", encoding="utf-8") as file:
        file.write('\nrgb=[0.1,0.2,0.3]\n')
    assert not await reloader.reload_once()
    assert "重启" in reloader.last_error
    assert reloader.executor.world.version == 0


async def test_refresh_fences_submit_and_old_context_afterwards(make_env):
    env = make_env()
    try:
        request = action(env)
        env.state_refreshing = True
        assert (await env.submit(request)).reason == "resource_busy"
        env.world.version += 1
        env.state_refreshing = False
        assert (await env.submit(request)).reason == "stale_state"
        assert not env.executions
    finally:
        await env.close()


def test_pose_updates_reuse_geometry(monkeypatch):
    from wrs_agent.schemas import ObjectData

    calls = []
    obj = SimpleNamespace(set_pos_rotmat=lambda **kwargs: calls.append(kwargs))
    original = ObjectData(source="configuration", pos=[0.0] * 3,
                          rotmat=[[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]],
                          geometry={"kind": "box", "xyz_lengths": [0.1] * 3})
    displayed = {"A": (original, obj)}
    changed = original.model_copy(update={"pos": [1.0, 2.0, 3.0]})

    def cannot_rebuild(*args):
        raise AssertionError("pose edit must not reload STL")

    monkeypatch.setattr(adapter, "make_scene_object", cannot_rebuild)
    adapter.sync_scene_objects(None, None, {"A": changed}, displayed)
    assert displayed["A"][1] is obj and calls[0]["pos"] == [1, 2, 3]


async def test_environment_closes_watcher_before_model_owner(tmp_path, monkeypatch):
    closed = asyncio.Event()

    class Model:
        def read(self):
            return {}

        def load_scene(self, objects):
            return objects

    async def open_model(scene):
        state = adapter.VirtualState({})
        state.scene_path, state.scene_config = tmp_path / "scene.toml", {}

        async def close():
            assert not any(t.get_name() == "wrs-scene-layout" for t in asyncio.all_tasks())
            closed.set()

        return None, Model(), state, close

    monkeypatch.setattr(adapter, "open_model", open_model)
    env = await adapter.make_wrs_environment(tmp_path / "journal.sqlite3")
    await env.close()
    assert closed.is_set()
