"""Finite real viewer HTTP/WebSocket check; no robot, camera, renderer or audio nodes."""

import asyncio
import contextlib
import io
import logging
import socket

import httpx

from wrs_agent.env.wrs import load_wrs, serve_viewer_hub


async def main():
    load_wrs()
    from websockets.asyncio.client import connect

    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    output = io.StringIO()
    log_received = asyncio.Event()

    def notify_record(record):
        log_received.set()
        return True

    handler = logging.StreamHandler(output)
    handler.setLevel(logging.ERROR)
    handler.addFilter(notify_record)
    root = logging.getLogger()
    root.addHandler(handler)

    def http_response(path, original):
        if path == "/broken":
            raise RuntimeError("fixture_http_failure")
        return original

    serving = asyncio.create_task(serve_viewer_hub(
        port, http_handler=http_response,
    ))
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as http:
            async with asyncio.timeout(10):
                while True:
                    if serving.done():
                        await serving
                    try:
                        assert (await http.get("/")).status_code == 200
                        break
                    except httpx.ConnectError:
                        await asyncio.sleep(0.02)

            # Exactly like World._hub_is_up: connect and close without a request.
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.1)
            assert "opening handshake failed" not in output.getvalue(), output.getvalue()

            # Both malformed and incomplete, nonempty request lines must stay visible.
            for number, request in enumerate((b"not-http\r\n\r\n", b"GET /"), 1):
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(request)
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                async with asyncio.timeout(3):
                    while output.getvalue().count("opening handshake failed") < number:
                        log_received.clear()
                        await log_received.wait()

            assert (await http.get("/broken")).status_code == 500
            async with asyncio.timeout(3):
                while "fixture_http_failure" not in output.getvalue():
                    log_received.clear()
                    await log_received.wait()
            assert (await http.get("/")).status_code == 200
            async with connect(f"ws://127.0.0.1:{port}/", proxy=None):
                pass
            assert not serving.done()
            assert output.getvalue().count("opening handshake failed") == 3
    finally:
        serving.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await serving
        root.removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    asyncio.run(main())
    print("Viewer handshake: empty probe quiet; malformed logged; HTTP/WebSocket PASS")
