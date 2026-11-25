"""Optional Home Assistant WebSocket transport -- for users who already run
Home Assistant and would rather reuse its existing MQTT connection than
configure a second, direct one. DirectMqttProtocol (mqtt_direct.py) is the
recommended default for a standalone installation; this module is for
anyone who prefers the HA-integrated path instead. Not required for a
standalone install.

Configuration: pass url/token explicitly, or set the HA_URL and HA_TOKEN
environment variables (e.g. in a `.env` file loaded by your process
manager, or Docker's `--env-file`). No path or token is hardcoded here --
bring your own HA instance and your own long-lived access token (Home
Assistant: Profile -> Security -> Long-Lived Access Tokens).
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from .protocol import HaWsTransport, Subscription, Message


class HaLiveTransport(HaWsTransport):
    """Synchronous facade over the async websockets client, matching the
    call style the rest of this package expects (it was written sync-first
    deliberately -- a CLI tool doesn't need an event loop, and keeping sync
    here means protocol.py/sync.py never have to be async either).

    Single-reader dispatcher, not ad hoc recv() calls: exactly one coroutine
    (_drain_one) ever calls self._ws.recv(). Every message it reads is
    routed by type -- "result" messages matching a pending command id go
    into self._results, "event" messages go into self._events -- and never
    discarded. (An earlier version looped on raw recv() until it saw its
    own id, silently dropping any event message that happened to arrive
    first -- a real race, since a device's settings-query reply can arrive
    before the mqtt.publish service-call's own ack. Fake-transport unit
    tests can't model that kind of interleaved async delivery; only
    testing against a real connection found it.)
    """

    def __init__(self, url: str | None = None, token: str | None = None,
                 reconnect_attempts: int = 2, reconnect_backoff_s: float = 2.0):
        import asyncio
        self._asyncio = asyncio
        self._url = url or os.environ.get("HA_URL", "http://homeassistant.local:8123")
        self._token = token or os.environ.get("HA_TOKEN", "")
        if not self._token:
            raise RuntimeError("Home Assistant access token not provided -- pass token= or set HA_TOKEN")
        self._loop = asyncio.new_event_loop()
        self._ws = None
        self._mid = 1
        self._results: dict[int, dict] = {}
        # Plain list, not asyncio.Queue: a Queue constructed outside a
        # running loop (as __init__ necessarily is) can bind itself to the
        # wrong event loop under Python 3.9's implicit-loop-binding
        # behavior, silently breaking put/get coordination. A plain list
        # has no loop affinity at all, and everything here runs single-
        # threaded/cooperatively, so no lock is needed.
        self._events: list[dict] = []
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_backoff_s = reconnect_backoff_s
        self.reconnect_count = 0  # exposed for diagnostics/UI "connection health" display

    def __enter__(self):
        self._loop.run_until_complete(self._connect())
        return self

    def __exit__(self, *exc):
        try:
            self._loop.run_until_complete(self._ws.close())
        except Exception:
            pass
        self._loop.close()
        return False

    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def _connect(self):
        import websockets
        ws_url = self._url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
        self._ws = await websockets.connect(ws_url, max_size=10_000_000)
        msg = json.loads(await self._ws.recv())
        assert msg["type"] == "auth_required"
        await self._ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        msg = json.loads(await self._ws.recv())
        if msg["type"] != "auth_ok":
            raise RuntimeError(f"Home Assistant auth failed: {msg}")
        # A reconnect gets a fresh connection with no outstanding
        # subscriptions -- any caller still mid-operation across a
        # reconnect will simply time out and can retry, rather than this
        # transport pretending old subscription ids are still valid.
        self._results.clear()
        self._events.clear()

    async def _reconnect_with_backoff(self):
        last_exc = None
        for attempt in range(1, self.reconnect_attempts + 1):
            try:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                await self._connect()
                self.reconnect_count += 1
                return
            except Exception as e:
                last_exc = e
                await self._asyncio.sleep(self.reconnect_backoff_s * attempt)
        raise RuntimeError(f"reconnect failed after {self.reconnect_attempts} attempts: {last_exc}")

    async def _drain_one(self, timeout: float) -> bool:
        """Reads exactly one message and routes it. Returns False on
        timeout (nothing read), True otherwise -- callers loop this until
        their own condition (a specific result id, or any event) is met.
        On a detected disconnect, reconnects once (with backoff) and
        re-raises a clear error for the caller's own retry logic to handle
        -- reliable recovery means the TRANSPORT heals itself, but the
        in-flight request still needs to be reissued by the caller
        (reliability.py's retry loop already does this for settings
        queries)."""
        try:
            raw = await self._asyncio.wait_for(self._ws.recv(), timeout=timeout)
        except self._asyncio.TimeoutError:
            return False
        except Exception as e:  # ConnectionClosed and friends
            await self._reconnect_with_backoff()
            raise ConnectionResetError(f"Home Assistant WebSocket disconnected and was reconnected; "
                                        f"the in-flight request must be retried: {e}") from e
        m = json.loads(raw)
        if m.get("type") == "result" and "id" in m:
            self._results[m["id"]] = m
        elif m.get("type") == "event":
            self._events.append(m.get("event", {}))
        return True

    async def _call(self, cmd: dict, timeout: float = 10.0) -> dict:
        self._mid += 1
        mid = self._mid
        await self._ws.send(json.dumps({**cmd, "id": mid}))
        deadline = self._loop.time() + timeout
        while mid not in self._results:
            remaining = deadline - self._loop.time()
            if remaining <= 0 or not await self._drain_one(remaining):
                raise RuntimeError(f"no result for request id {mid} within {timeout}s: {cmd}")
        return self._results.pop(mid)

    async def _next_event(self, timeout: float) -> Optional[dict]:
        deadline = self._loop.time() + timeout
        while not self._events:
            remaining = deadline - self._loop.time()
            if remaining <= 0 or not await self._drain_one(remaining):
                return None
        return self._events.pop(0)

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        resp = self._loop.run_until_complete(self._call({
            "type": "call_service", "domain": "mqtt", "service": "publish",
            "service_data": {"topic": topic, "payload": payload, "qos": 0, "retain": retain},
        }))
        if not resp.get("success"):
            raise RuntimeError(f"mqtt.publish failed: {resp}")

    def subscribe(self, topic: str) -> "Subscription":
        resp = self._loop.run_until_complete(self._call({"type": "mqtt/subscribe", "topic": topic}))
        if not resp.get("success"):
            raise RuntimeError(f"mqtt/subscribe failed: {resp}")
        return _HaLiveSubscription(self, subscription_id=resp["id"])


class _HaLiveSubscription(Subscription):
    def __init__(self, transport: HaLiveTransport, subscription_id: int):
        self._t = transport
        self._id = subscription_id
        self._closed = False

    def next(self, timeout: float) -> Optional[Message]:
        ev = self._t._loop.run_until_complete(self._t._next_event(timeout))
        if ev is None:
            return None
        return Message(topic=ev.get("topic", ""), payload=ev.get("payload", ""))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._t._loop.run_until_complete(
                self._t._call({"type": "unsubscribe_events", "subscription": self._id}))
        except Exception:
            pass  # best-effort cleanup -- a failed unsubscribe (e.g. connection
                   # already dropped) must not raise out of a `with` block's exit
