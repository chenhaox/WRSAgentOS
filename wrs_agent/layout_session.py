"""Own a disposable kitchen scene for one run of the 05 example."""

import hashlib
import json
import shutil
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path

from wrs_agent.instance_lock import InstanceLock
from wrs_agent.scene import SceneConfig, load_scene

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENE = ROOT / "wrs_agent/assets/kitchen/scene.toml"
SESSION_DIRECTORY = ROOT / ".local/kitchen-preview"


def lock_name(directory):
    digest = hashlib.sha256(str(Path(directory).resolve()).casefold().encode()).hexdigest()
    return "kitchen-preview-" + digest


def current_session(directory=SESSION_DIRECTORY):
    directory = Path(directory).resolve()
    try:
        lock = InstanceLock(lock_name(directory))
    except RuntimeError as error:
        if str(error) != "node_already_running":
            raise
    else:
        lock.close()
        raise ValueError("没有正在运行的临时场景，请先启动 05_start_node.py。")
    pointer = json.loads((directory / "current.json").read_text(encoding="utf-8"))
    relative = pointer.get("scene") if isinstance(pointer, dict) else None
    if not isinstance(relative, str):
        raise ValueError("临时场景记录无效，请重新启动 05_start_node.py。")
    scene = (directory / relative).resolve()
    if not scene.is_relative_to(directory) or scene.parent == directory or not scene.is_file():
        raise ValueError("临时场景已关闭，请重新启动 05_start_node.py。")
    return scene


@contextmanager
def kitchen_session(scene=DEFAULT_SCENE, directory=SESSION_DIRECTORY):
    """Fresh copy on every start, even if the previous process was killed."""
    scene, directory = Path(scene).resolve(), Path(directory).resolve()
    lock = InstanceLock(lock_name(directory))
    pointer = directory / "current.json"
    try:
        objects = load_scene(scene)
        config = SceneConfig.model_validate(tomllib.loads(scene.read_text(encoding="utf-8")))
        if config.layout is None:
            raise ValueError("临时摆放编辑需要带 layout 的场景。")
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="run-", dir=directory) as temporary:
            target = Path(temporary).resolve()
            assert target.is_relative_to(directory)
            shutil.copyfile(scene.parent / config.layout, target / config.layout)
            runtime = target / "scene.toml"
            shutil.copyfile(scene, runtime)
            assert load_scene(runtime) == objects
            pending = directory / "current.tmp"
            pending.write_text(json.dumps({"scene": str(runtime.relative_to(directory))}),
                               encoding="utf-8")
            pending.replace(pointer)
            try:
                yield runtime
            finally:
                pointer.unlink(missing_ok=True)
    finally:
        lock.close()
