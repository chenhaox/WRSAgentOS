"""Reload placements on the virtual node; hardware backends never start this watcher."""

import asyncio
import logging

from wrs_agent.scene import load_scene

LOG = logging.getLogger(__name__)


class SceneReloader:
    def __init__(self, executor, call, model, path, configured_objects):
        self.executor, self.call, self.model = executor, call, model
        self.path, self.previous = path, configured_objects
        self.last_error = None

    async def reload_once(self):
        executor = self.executor
        if executor.active is not None or executor._closed:
            return False
        try:
            objects = await asyncio.to_thread(load_scene, self.path)
            if objects == self.previous:
                self.last_error = None
                return False
            # Only placement edits are live; geometry/material changes require a restart.
            if set(objects) != set(self.previous) or any(
                obj.model_dump(exclude={"pos", "rotmat"})
                != self.previous[name].model_dump(exclude={"pos", "rotmat"})
                for name, obj in objects.items()
            ):
                raise ValueError("模型、颜色或物体清单已改变，请重启 05 和 07；当前摆放不变。")
            if executor.active is not None or executor._closed:
                return False
            # No await between the idle check and fence: submit cannot enter mid-refresh.
            executor.state_refreshing = True
            try:
                resolved = await self.call(self.model.load_scene, objects)
                executor.world.objects = resolved
                executor.world.version += 1
                executor.leases.clear()
                self.previous = objects
                self.last_error = None
                LOG.info("厨具摆放已更新；刷新 8000 页面可查看。")
                return True
            finally:
                executor.state_refreshing = False
        except (OSError, ValueError) as error:
            message = str(error)
            if message != self.last_error:
                LOG.warning("厨具摆放未更新，保留上次场景：%s", message)
                self.last_error = message
            return False

    async def watch(self):
        while True:
            await asyncio.sleep(0.5)
            await self.reload_once()
