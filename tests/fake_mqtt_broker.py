"""A minimal, pure-stdlib MQTT 3.1.1 broker -- QoS 0 only, just enough of
the wire protocol for this project's own tests to run a real end-to-end
simulated-controller scenario without a real broker, Docker, or any new
dependency. Not for production use; this exists purely so `python -m
unittest` (and this project's CI) can prove DirectMqttProtocol and a
simulated Faikout controller talk to each other correctly over the actual
MQTT wire format, not just in-process fakes.

Supports: CONNECT/CONNACK, SUBSCRIBE/SUBACK (with +/# wildcard matching),
UNSUBSCRIBE/UNSUBACK, PUBLISH (QoS 0, retained messages supported and
replayed to new subscribers matching their topic filter), PINGREQ/PINGRESP,
DISCONNECT. Nothing else -- no QoS 1/2, no persistence across restarts, no
auth enforcement (accepts any CONNECT).
"""
from __future__ import annotations

import re
import socket
import struct
import threading


def _encode_remaining_length(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n % 128
        n //= 128
        if n > 0:
            byte |= 0x80
        out.append(byte)
        if n == 0:
            break
    return bytes(out)


def _read_remaining_length(recv_byte) -> int:
    multiplier = 1
    value = 0
    while True:
        b = recv_byte()
        value += (b & 0x7F) * multiplier
        if (b & 0x80) == 0:
            break
        multiplier *= 128
    return value


def _topic_matches(filter_: str, topic: str) -> bool:
    f_parts = filter_.split("/")
    t_parts = topic.split("/")
    for i, fp in enumerate(f_parts):
        if fp == "#":
            return True
        if i >= len(t_parts):
            return False
        if fp == "+":
            continue
        if fp != t_parts[i]:
            return False
    return len(f_parts) == len(t_parts)


class _ClientConn:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.subscriptions: list[str] = []
        self.lock = threading.Lock()

    def send(self, data: bytes) -> None:
        with self.lock:
            try:
                self.sock.sendall(data)
            except OSError:
                pass


class FakeMqttBroker:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, port))
        self._server.listen(8)
        self.port = self._server.getsockname()[1]
        self._clients: list[_ClientConn] = []
        self._retained: dict[str, bytes] = {}
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        try:
            self._server.close()
        except OSError:
            pass
        with self._lock:
            for c in self._clients:
                try:
                    c.sock.close()
                except OSError:
                    pass

    def _accept_loop(self):
        while self._running:
            try:
                sock, _ = self._server.accept()
            except OSError:
                return
            t = threading.Thread(target=self._client_loop, args=(sock,), daemon=True)
            t.start()

    def _client_loop(self, sock: socket.socket):
        conn = _ClientConn(sock)
        with self._lock:
            self._clients.append(conn)
        try:
            while self._running:
                header = sock.recv(1)
                if not header:
                    break
                packet_type = header[0] >> 4

                def recv_byte():
                    b = sock.recv(1)
                    if not b:
                        raise ConnectionError("closed")
                    return b[0]

                remaining = _read_remaining_length(recv_byte)
                body = b""
                while len(body) < remaining:
                    chunk = sock.recv(remaining - len(body))
                    if not chunk:
                        raise ConnectionError("closed")
                    body += chunk

                if packet_type == 1:  # CONNECT
                    conn.send(bytes([0x20, 0x02, 0x00, 0x00]))  # CONNACK, success
                elif packet_type == 8:  # SUBSCRIBE
                    packet_id = body[0:2]
                    pos = 2
                    granted = bytearray()
                    topics = []
                    while pos < len(body):
                        tlen = struct.unpack("!H", body[pos:pos + 2])[0]
                        pos += 2
                        topic = body[pos:pos + tlen].decode("utf-8")
                        pos += tlen
                        pos += 1  # requested qos byte, ignored
                        topics.append(topic)
                        granted.append(0x00)
                    with self._lock:
                        conn.subscriptions.extend(topics)
                    conn.send(bytes([0x90]) + _encode_remaining_length(2 + len(granted)) + packet_id + bytes(granted))
                    # replay retained messages matching the new filter(s)
                    with self._lock:
                        retained_snapshot = dict(self._retained)
                    for topic, payload in retained_snapshot.items():
                        for filt in topics:
                            if _topic_matches(filt, topic):
                                conn.send(self._build_publish(topic, payload, retain=True))
                                break
                elif packet_type == 10:  # UNSUBSCRIBE
                    packet_id = body[0:2]
                    pos = 2
                    while pos < len(body):
                        tlen = struct.unpack("!H", body[pos:pos + 2])[0]
                        pos += 2
                        topic = body[pos:pos + tlen].decode("utf-8")
                        pos += tlen
                        with self._lock:
                            if topic in conn.subscriptions:
                                conn.subscriptions.remove(topic)
                    conn.send(bytes([0xB0, 0x02]) + packet_id)
                elif packet_type == 3:  # PUBLISH (QoS 0 only supported)
                    flags = header[0] & 0x0F
                    retain = bool(flags & 0x01)
                    tlen = struct.unpack("!H", body[0:2])[0]
                    topic = body[2:2 + tlen].decode("utf-8")
                    payload = body[2 + tlen:]
                    if retain:
                        with self._lock:
                            if payload:
                                self._retained[topic] = payload
                            else:
                                self._retained.pop(topic, None)
                    with self._lock:
                        targets = [c for c in self._clients
                                   if any(_topic_matches(f, topic) for f in c.subscriptions)]
                    for target in targets:
                        target.send(self._build_publish(topic, payload, retain=retain))
                elif packet_type == 12:  # PINGREQ
                    conn.send(bytes([0xD0, 0x00]))
                elif packet_type == 14:  # DISCONNECT
                    break
                # other packet types (PUBACK etc.) intentionally unsupported -- QoS 0 only
        except (ConnectionError, OSError):
            pass
        finally:
            with self._lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _build_publish(topic: str, payload: bytes, retain: bool) -> bytes:
        topic_bytes = topic.encode("utf-8")
        body = struct.pack("!H", len(topic_bytes)) + topic_bytes + payload
        flag_byte = 0x30 | (0x01 if retain else 0x00)
        return bytes([flag_byte]) + _encode_remaining_length(len(body)) + body
