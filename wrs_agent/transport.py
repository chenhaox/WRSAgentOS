"""A bounded Zenoh 1.9 wrapper for the trusted loopback Mock profile."""

import asyncio
import contextlib
import json
import re
import secrets
import threading
from collections import deque

import zenoh

from wrs_agent.errors import AgentError, error_info, from_exception
from wrs_agent.schemas import MAX_BYTES, Envelope, ErrorInfo, decode, encode, new_id

MAX_DATA_BYTES = 32 * 1024 * 1024


class RemoteError(AgentError):
    pass


class Inbox:
    """One coalesced wakeup, bounded data, no asyncio objects touched by callbacks."""

    def __init__(self, capacity, loop):
        self.capacity = capacity
        self.loop = loop
        self.items = deque()
        self.lock = threading.Lock()
        self.event = asyncio.Event()
        self.notified = False
        self.closed = False
        self.high_water = 0
        self.rejected = 0

    def put(self, item):
        with self.lock:
            if self.closed or len(self.items) >= self.capacity:
                self.rejected += 1
                return False
            self.items.append(item)
            self.high_water = max(self.high_water, len(self.items))
            if not self.notified:
                self.notified = True
                self.loop.call_soon_threadsafe(self.event.set)
            return True

    async def get(self):
        while True:
            with self.lock:
                if self.items:
                    return self.items.popleft()
                self.event.clear()
                self.notified = False
            await self.event.wait()

    def close(self):
        with self.lock:
            self.closed = True
            self.items.clear()


def loopback_config(endpoint):
    if not re.fullmatch(r"tcp/127\.0\.0\.1:[0-9]{1,5}", endpoint):
        raise ValueError("Only explicit IPv4 loopback endpoints are enabled in M0-M2")
    config = zenoh.Config()
    for key, value in {
        "mode": "client",
        "connect/endpoints": [endpoint],
        "listen/endpoints": [],
        "scouting/multicast/enabled": False,
        "scouting/gossip/enabled": False,
    }.items():
        config.insert_json5(key, json.dumps(value))
    return config


class Transport:
    def __init__(self, endpoint, site, env_id, token, source):
        if not all(re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", x) for x in (site, env_id, source)):
            raise ValueError("invalid_namespace")
        if len(token) < 16:
            raise ValueError(
                "Set WRS_AGENT_TOKEN to a session credential of at least 16 characters"
            )
        self.session = zenoh.open(loopback_config(endpoint))
        self.endpoint, self.site = endpoint, site
        self.prefix = f"wrs/v4/{site}/{env_id}"
        self.env_id, self.token, self.source = env_id, token, source
        self.session_id = new_id()
        self.loop = asyncio.get_running_loop()
        self.normal = Inbox(64, self.loop)
        self.control = Inbox(16, self.loop)
        self.data = Inbox(2, self.loop)
        self.handles = []
        self.workers = [
            asyncio.create_task(self._serve(self.normal)),
            asyncio.create_task(self._serve(self.normal)),
            asyncio.create_task(self._serve(self.control)),
            asyncio.create_task(self._serve(self.data, binary=True)),
        ]
        self.normal_slots = asyncio.Semaphore(32)
        self.control_slots = asyncio.Semaphore(4)
        self.data_slots = asyncio.Semaphore(2)
        self.callback_threads = set()
        self.loop_thread = threading.get_ident()

    def key(self, suffix):
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", suffix) or ".." in suffix:
            raise ValueError("exact_service_key_required")
        return f"{self.prefix}/{suffix}"

    def register_handler(self, suffix, handler, *, control=False, binary=False):
        """Binary replies have their own bounded worker; requests remain small and authenticated."""
        if binary and control:
            raise ValueError("data_is_not_control")
        key = self.key(suffix)
        inbox = self.data if binary else self.control if control else self.normal

        def receive(query):
            self.callback_threads.add(threading.get_ident())
            if query.payload is None or len(query.payload) > MAX_BYTES:
                query.reply(
                    key,
                    encode({"ok": False, "error": error_info("invalid_payload_size").model_dump()}),
                )
            elif not inbox.put((query, key, handler)):
                query.reply(
                    key, encode({"ok": False, "error": error_info("overloaded").model_dump()})
                )

        handle = self.session.declare_queryable(
            key, zenoh.handlers.Callback(receive, indirect=False), complete=True
        )
        self.handles.append(handle)

    async def _serve(self, inbox, *, binary=False):
        while True:
            query, key, handler = await inbox.get()
            try:
                raw = query.payload.to_bytes()
                if len(raw) > MAX_BYTES:
                    raise AgentError("payload_too_large")
                envelope = Envelope.model_validate_json(raw)
                if envelope.env_id != self.env_id or not secrets.compare_digest(
                    envelope.auth, self.token
                ):
                    raise AgentError("unauthorized")
                result = await handler(envelope.payload)
                if binary:
                    if not isinstance(result, bytes) or len(result) > MAX_DATA_BYTES:
                        raise AgentError("payload_too_large")
                    reply, encoding = result, zenoh.Encoding.APPLICATION_OCTET_STREAM
                else:
                    reply = encode({"ok": True, "result": result})
                    encoding = zenoh.Encoding.APPLICATION_JSON
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Validation errors can contain input (including credentials). Do not echo them.
                error = from_exception(exc, node_id=getattr(self, "node_id", None))
                reply = encode({"ok": False, "error": error.model_dump()})
                encoding = zenoh.Encoding.APPLICATION_JSON
            with contextlib.suppress(Exception):
                # Zenoh 1.9 replies inherit request QoS, including background data queries.
                query.reply(key, reply, encoding=encoding)
            del query

    async def request(self, suffix, payload, *, timeout=2.0, control=False, category="interactive"):
        return await self._request(
            suffix, payload, timeout=timeout, control=control, category=category,
        )

    async def request_bytes(self, suffix, payload, *, timeout=2.0):
        return await self._request(
            suffix, payload, timeout=timeout, category="background", binary=True,
        )

    async def _request(
        self, suffix, payload, *, timeout, control=False, category="interactive", binary=False,
    ):
        key = self.key(suffix)
        if category not in {"control", "interactive", "background"}:
            raise ValueError("unknown_message_category")
        category = "control" if control else category
        slots = self.data_slots if binary else self.control_slots if control else self.normal_slots
        async with asyncio.timeout(timeout):
            async with slots:
                envelope = Envelope(
                    source=self.source,
                    session=self.session_id,
                    env_id=self.env_id,
                    auth=self.token,
                    payload=payload,
                    category=category,
                )
                cancel = zenoh.CancellationToken()
                replies = self.session.get(
                    key,
                    zenoh.handlers.FifoChannel(1),
                    payload=encode(envelope),
                    timeout=timeout,
                    cancellation_token=cancel,
                    consolidation=zenoh.ConsolidationMode.NONE,
                    congestion_control=zenoh.CongestionControl.DROP,
                    priority={
                        "control": zenoh.Priority.REAL_TIME,
                        "interactive": zenoh.Priority.INTERACTIVE_HIGH,
                        "background": zenoh.Priority.BACKGROUND,
                    }[category],
                )
                try:
                    while True:
                        try:
                            reply = replies.try_recv()
                        except zenoh.ZError:
                            raise TimeoutError(f"No reply from {suffix}") from None
                        if reply is not None:
                            if reply.ok is None:
                                raise RemoteError("query_error")
                            is_data = reply.ok.encoding == zenoh.Encoding.APPLICATION_OCTET_STREAM
                            if binary and is_data:
                                if len(reply.ok.payload) > MAX_DATA_BYTES:
                                    raise RemoteError("payload_too_large")
                                return reply.ok.payload.to_bytes()
                            message = decode(reply.ok.payload.to_bytes())
                            if not message.get("ok"):
                                try:
                                    error = ErrorInfo.model_validate(message.get("error"))
                                except ValueError:
                                    raise RemoteError("invalid_reply") from None
                                raise RemoteError(error)
                            if binary:
                                raise RemoteError("invalid_reply")
                            return message["result"]
                        await asyncio.sleep(0.005)
                finally:
                    cancel.cancel()

    def publish(self, suffix, payload, *, category="background"):
        self.session.put(
            self.key(suffix),
            encode(payload),
            congestion_control=zenoh.CongestionControl.DROP,
            priority={
                "control": zenoh.Priority.REAL_TIME,
                "interactive": zenoh.Priority.INTERACTIVE_HIGH,
                "background": zenoh.Priority.BACKGROUND,
            }[category],
        )

    def subscribe(self, suffix, capacity=1):
        # Latest-only observations/events; action status queries are authoritative.
        handle = self.session.declare_subscriber(
            self.key(suffix), zenoh.handlers.RingChannel(capacity)
        )
        self.handles.append(handle)
        return handle

    async def close(self):
        self.normal.close()
        self.control.close()
        self.data.close()
        for handle in self.handles:
            handle.undeclare()
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.session.close()
