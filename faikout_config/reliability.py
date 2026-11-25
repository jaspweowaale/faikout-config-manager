"""Reliable settings querying: rate limiting, serialization, retry/backoff,
caching, and fleet-level partial-success handling.

## Why this exists (read before changing the retry/backoff numbers below)

MQTT delivery for this protocol is not guaranteed: the settings-query
trigger and the device's reply are both typically published at QoS 0, which
has no delivery guarantee at the protocol level. During this project's own
development, a working theory that a specific controller enforced a
deliberate "cooldown" on repeated queries did NOT survive a controlled,
spaced test built specifically to check it -- the actual failure pattern
(a query failing immediately after a different query to the same device had
just succeeded, and then failing repeatedly despite waiting far longer than
any plausible cooldown) was a much better match for ordinary intermittent
message loss (plausibly WiFi-signal-related on weaker devices) than for a
deliberate rate limit.

**Because the true cause of any given transport hiccup usually isn't
pinned down with certainty, this module is built to be correct regardless
of the specific cause**: rate limiting helps if a device-side cooldown
exists; serialization + retry/backoff helps if it's packet loss; the cache
+ explicit status model helps either way by never conflating "we don't
currently have fresh data" with "the device has no configuration" or "the
device confirmed this setting is unset." If you're debugging a flaky
controller, don't assume either explanation -- check both, and consider
that per-device WiFi signal strength is often the simplest first thing to
rule in or out.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from .protocol import DeviceConfigProtocol, ProtocolError, RawSettings


class QueryStatus(Enum):
    NEVER_QUERIED = "never_queried"
    FRESH = "fresh"       # a query succeeded within the configured freshness window
    STALE = "stale"       # a query succeeded once, but not recently -- cache still usable, flagged
    FAILED = "failed"     # the most recent attempt failed; see CacheEntry.last_error
    BUSY = "busy"         # a query for this hostname is already in flight; this attempt was rejected, not retried


@dataclass
class CacheEntry:
    hostname: str
    raw: dict | None = None          # last SUCCESSFULLY read settings; never cleared by a failed query
    last_success_at: float | None = None
    last_attempt_at: float | None = None
    last_error: str | None = None
    attempts_since_success: int = 0

    def status(self, now: float, fresh_window_s: float) -> QueryStatus:
        if self.last_success_at is None:
            return QueryStatus.FAILED if self.last_attempt_at is not None else QueryStatus.NEVER_QUERIED
        if self.last_attempt_at is not None and self.last_attempt_at > self.last_success_at:
            return QueryStatus.FAILED  # a more recent attempt failed -- say so, even though old data exists
        return QueryStatus.FRESH if (now - self.last_success_at) <= fresh_window_s else QueryStatus.STALE


@dataclass
class QueryResult:
    hostname: str
    success: bool
    raw: dict | None            # None on failure -- NEVER an empty dict standing in for "no reply"
    from_cache: bool            # True if this result reuses a previous success rather than a fresh query
    error: str | None = None
    attempts: int = 1
    elapsed_s: float = 0.0


class ReliableSettingsClient:
    """Wraps any DeviceConfigProtocol (fake or real) with the reliability
    properties required before this tool can be trusted to drive production
    decisions (sync eligibility, drift findings, baselines) from live reads.
    """

    def __init__(self, protocol: DeviceConfigProtocol,
                 min_interval_s: float = 30.0,
                 fresh_window_s: float = 300.0,
                 timeout_s: float = 8.0,
                 max_retries: int = 3,
                 backoff_base_s: float = 2.0,
                 backoff_jitter_s: float = 0.5):
        self._protocol = protocol
        self.min_interval_s = min_interval_s
        self.fresh_window_s = fresh_window_s
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_jitter_s = backoff_jitter_s

        self._cache: dict[str, CacheEntry] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._cache_lock = threading.Lock()  # guards dict-of-locks creation, not the queries themselves

    def _lock_for(self, hostname: str) -> threading.Lock:
        with self._cache_lock:
            return self._locks.setdefault(hostname, threading.Lock())

    def _entry_for(self, hostname: str) -> CacheEntry:
        with self._cache_lock:
            return self._cache.setdefault(hostname, CacheEntry(hostname=hostname))

    def get_cached(self, hostname: str) -> CacheEntry:
        """Read-only, no network -- for UI display of last-refresh info
        without triggering a query."""
        return self._entry_for(hostname)

    def forget(self, hostname: str) -> None:
        """Drops cached state for a hostname -- call this when a device is
        removed from the registry so a stale cache entry can't outlive it."""
        with self._cache_lock:
            self._cache.pop(hostname, None)
            self._locks.pop(hostname, None)

    def get_settings(self, hostname: str, force: bool = False) -> QueryResult:
        """Prevention of overlapping requests: a non-blocking lock acquire
        -- if a query for this exact hostname is already in flight (e.g.
        this method called concurrently from two threads, as a web UI
        backend might), this call returns immediately with success=False,
        error="busy", rather than queuing or racing a second request
        against the same device. Per-controller serialization follows from
        the lock being per-hostname: two DIFFERENT hostnames never block
        each other.
        """
        lock = self._lock_for(hostname)
        if not lock.acquire(blocking=False):
            return QueryResult(hostname=hostname, success=False, raw=None, from_cache=False,
                                error="busy: a query for this controller is already in flight")
        try:
            return self._get_settings_locked(hostname, force)
        finally:
            lock.release()

    def _get_settings_locked(self, hostname: str, force: bool) -> QueryResult:
        entry = self._entry_for(hostname)
        now = time.monotonic()

        if not force and entry.last_attempt_at is not None:
            elapsed_since_attempt = now - entry.last_attempt_at
            if elapsed_since_attempt < self.min_interval_s:
                # Configurable minimum interval: return the cache rather
                # than hit the network again so soon. A cache hit is NOT
                # the same thing as a successful fresh query -- callers
                # that care about freshness check CacheEntry.status().
                return QueryResult(hostname=hostname, success=entry.raw is not None, raw=entry.raw,
                                    from_cache=True,
                                    error=None if entry.raw is not None else "no successful query yet, "
                                          "and minimum query interval not yet elapsed")

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            entry.last_attempt_at = time.monotonic()
            t0 = time.monotonic()
            try:
                raw: RawSettings = self._protocol.read_settings(hostname)
                entry.raw = raw.raw
                entry.last_success_at = time.monotonic()
                entry.attempts_since_success = 0
                entry.last_error = None
                return QueryResult(hostname=hostname, success=True, raw=raw.raw, from_cache=False,
                                    attempts=attempt, elapsed_s=time.monotonic() - t0)
            except ProtocolError as e:
                last_error = str(e)
                entry.attempts_since_success += 1
            if attempt < self.max_retries:
                backoff = self.backoff_base_s * (2 ** (attempt - 1)) + random.uniform(0, self.backoff_jitter_s)
                time.sleep(backoff)

        entry.last_error = last_error
        # A failed query NEVER clears entry.raw -- old data (if any) stays
        # available via get_cached(), explicitly marked FAILED/STALE by
        # status(), never silently presented as "this device has no
        # configuration" or "this setting is unset."
        return QueryResult(hostname=hostname, success=False, raw=None, from_cache=False,
                            error=last_error, attempts=self.max_retries)

    def query_fleet(self, hostnames: list[str], force: bool = False) -> dict[str, QueryResult]:
        """Sequential, not concurrent -- each controller is queried one at
        a time (serialization is also enforced per-hostname by the lock,
        but sequencing across different hostnames here as well avoids
        bursting the broker/network with simultaneous queries). One
        controller's failure never prevents the others from being queried
        or from reporting their own result -- this is the "safe handling of
        partial success" requirement: callers get a per-hostname
        QueryResult dict, not a single pass/fail for the whole fleet.
        """
        results = {}
        for hostname in hostnames:
            results[hostname] = self.get_settings(hostname, force=force)
        return results
