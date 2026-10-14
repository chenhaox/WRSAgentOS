"""Own the loopback WRS page service; scene construction belongs in the example."""

import asyncio
import contextlib
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager

from wrs_agent.env.wrs import serve_viewer_hub


@contextmanager
def viewer_hub(port, *, camera=None):
    """Serve a WRS page for this context only; refuse an already occupied port."""
    stopped, ready = threading.Event(), Future()

    async def run():
        preview = serving = None
        try:
            if camera is not None:
                from wrs_agent.nodes.camera.preview import CameraPreview

                preview = CameraPreview(**camera)
                await preview.start()
            serving = asyncio.create_task(serve_viewer_hub(
                port, **({"http_handler": preview.http_response} if preview else {}),
            ))
            # Binding errors must be reported before displaying a ready URL.
            await asyncio.sleep(0.1)
            if serving.done():
                await serving
                raise RuntimeError("viewer_hub_stopped")
            ready.set_result(None)
            while not stopped.is_set():
                done, _ = await asyncio.wait({serving}, timeout=0.1)
                if done:
                    await serving
                    raise RuntimeError("viewer_hub_stopped")
        except BaseException as error:
            if not ready.done():
                ready.set_exception(error)
            raise
        finally:
            try:
                if serving is not None:
                    serving.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await serving
            finally:
                if preview is not None and hasattr(preview, "worker"):
                    await preview.close()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="wrs-viewer-hub") as worker:
        running = worker.submit(asyncio.run, run())
        try:
            ready.result(timeout=10)
            yield
        finally:
            stopped.set()
            running.result(timeout=5)
