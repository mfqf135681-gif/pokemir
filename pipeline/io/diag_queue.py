"""DiagQueue — async-batched diagnostic event writer.

Replaces inline `events.diag.emit(...)` (38 sites in orchestrator,
non-顺序-sensitive per T79 audit). Main thread calls `enqueue(...)`
which is O(1); a background thread drains in batches.

Drop-on-full policy: when queue is saturated, new events are dropped (with
counter increment) rather than blocking the main capture loop. Main loop
latency > completeness for diag, by policy.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from uuid import UUID


@dataclass(frozen=True)
class DiagEvent:
    tag: str
    payload: dict[str, Any]
    hand_id: Optional[UUID] = None
    level: str = "INFO"
    ts: float = field(default_factory=time.monotonic)


# Writer signature: receives a batch of events, writes them. Raises on fatal
# errors (caller catches + logs). Implementations: real (PG INSERT) / mock (test).
WriterFn = Callable[[list[DiagEvent]], None]


class DiagQueue:
    """Bounded async queue with background batched writer.

    Lifecycle:
        q = DiagQueue(writer_fn=my_writer, maxsize=1000, batch_size=20)
        q.start()
        ...
        q.enqueue(tag="showdown.accepted", payload={"seat": 3})
        ...
        q.stop(flush=True)  # graceful shutdown
    """

    def __init__(
        self,
        writer_fn: WriterFn,
        *,
        maxsize: int = 1000,
        batch_size: int = 20,
        drain_interval_s: float = 0.2,
    ):
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        if drain_interval_s <= 0:
            raise ValueError("drain_interval_s must be > 0")

        self._writer = writer_fn
        self._batch_size = batch_size
        self._drain_interval = drain_interval_s
        self._q: queue.Queue[DiagEvent] = queue.Queue(maxsize=maxsize)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Counters (read after stop or while running)
        self._enqueued = 0
        self._dropped = 0
        self._written = 0
        self._write_errors = 0
        self._lock = threading.Lock()  # protects counters

    # ─── Public API ───────────────────────────────────────────────

    def enqueue(
        self,
        tag: str,
        payload: dict[str, Any],
        *,
        hand_id: Optional[UUID] = None,
        level: str = "INFO",
    ) -> bool:
        """Drop-on-full enqueue. Returns True if accepted, False if dropped."""
        ev = DiagEvent(tag=tag, payload=payload, hand_id=hand_id, level=level)
        try:
            self._q.put_nowait(ev)
            with self._lock:
                self._enqueued += 1
            return True
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("DiagQueue already started")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._drain_loop, name="DiagQueue.drain", daemon=True
        )
        self._thread.start()

    def stop(self, *, flush: bool = True, timeout_s: float = 5.0) -> None:
        """Signal stop. If flush=True, drain remaining events before join."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            if flush:
                self._drain_remaining()
            self._thread = None

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "enqueued": self._enqueued,
                "dropped": self._dropped,
                "written": self._written,
                "write_errors": self._write_errors,
                "queued_now": self._q.qsize(),
            }

    # ─── Internals ────────────────────────────────────────────────

    def _drain_loop(self) -> None:
        """Bg thread: pull up to batch_size events, write, sleep, repeat."""
        while not self._stop_event.is_set():
            batch = self._collect_batch()
            if batch:
                self._write_batch(batch)
            else:
                # Empty — sleep before next poll
                self._stop_event.wait(self._drain_interval)

    def _collect_batch(self) -> list[DiagEvent]:
        batch: list[DiagEvent] = []
        for _ in range(self._batch_size):
            try:
                ev = self._q.get_nowait()
                batch.append(ev)
            except queue.Empty:
                break
        return batch

    def _drain_remaining(self) -> None:
        """On stop(flush=True): drain all remaining events synchronously."""
        while True:
            batch = self._collect_batch()
            if not batch:
                break
            self._write_batch(batch)

    def _write_batch(self, batch: list[DiagEvent]) -> None:
        try:
            self._writer(batch)
            with self._lock:
                self._written += len(batch)
        except Exception:
            with self._lock:
                self._write_errors += 1
            # Per policy: never raise from bg thread. Caller's writer_fn should
            # have done its own logging; we just bump counter.
