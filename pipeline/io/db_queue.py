"""DBWriteQueue — generic async-batched DB write queue.

Replaces inline `session.commit()` (4 sites in orchestrator per T79 audit:
:351, :924, :1005, :2340) with deferred batched commits. Main thread
constructs a task (callable taking a Session) and enqueues; bg thread runs
N tasks per session+commit.

UNLIKE DiagQueue, this does NOT drop on full — DB writes are顺序-critical
(hand start → action events → hand end must be ordered). When the queue is
saturated, `enqueue()` blocks the caller until space frees up. This is
backpressure: main loop slows down rather than losing data.

Lifecycle is the same as DiagQueue.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# Task signature: receives a Session (or any unit-of-work handle from the
# caller's `session_factory`). Must not commit itself — that's done in batch.
TaskFn = Callable[[Any], None]


@dataclass(frozen=True)
class DBWriteTask:
    fn: TaskFn
    label: str = ""           # human-readable name for diag / debugging
    ts: float = field(default_factory=time.monotonic)


# session_factory: zero-arg callable returning a context-manager that yields
# a Session and commits on __exit__ (e.g. `with session_factory() as s: ...`).
SessionFactory = Callable[[], Any]


class DBWriteQueue:
    """Bounded blocking queue with bg batched session+commit writer.

    Lifecycle:
        q = DBWriteQueue(session_factory=make_session, batch_size=10)
        q.start()
        ...
        q.enqueue(lambda s: s.add(action_event), label="action:fold")
        ...
        q.stop(flush=True)
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        maxsize: int = 500,
        batch_size: int = 10,
        drain_interval_s: float = 0.1,
        enqueue_timeout_s: float = 5.0,
    ):
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        if drain_interval_s <= 0:
            raise ValueError("drain_interval_s must be > 0")

        self._session_factory = session_factory
        self._batch_size = batch_size
        self._drain_interval = drain_interval_s
        self._enqueue_timeout = enqueue_timeout_s
        self._q: queue.Queue[DBWriteTask] = queue.Queue(maxsize=maxsize)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._enqueued = 0
        self._blocked = 0      # times enqueue had to wait
        self._timed_out = 0    # times enqueue gave up (returned False)
        self._committed_batches = 0
        self._committed_tasks = 0
        self._failed_batches = 0
        self._lock = threading.Lock()

    # ─── Public API ───────────────────────────────────────────────

    def enqueue(self, fn: TaskFn, *, label: str = "") -> bool:
        """Block-on-full enqueue.

        Returns True on accept, False if `enqueue_timeout_s` elapsed without
        space (caller should treat as fatal — backpressure failure).
        """
        task = DBWriteTask(fn=fn, label=label)
        try:
            # Track if we had to wait at all
            if self._q.full():
                with self._lock:
                    self._blocked += 1
            self._q.put(task, timeout=self._enqueue_timeout)
            with self._lock:
                self._enqueued += 1
            return True
        except queue.Full:
            with self._lock:
                self._timed_out += 1
            return False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("DBWriteQueue already started")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._drain_loop, name="DBWriteQueue.drain", daemon=True
        )
        self._thread.start()

    def stop(self, *, flush: bool = True, timeout_s: float = 10.0) -> None:
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
                "blocked": self._blocked,
                "timed_out": self._timed_out,
                "committed_batches": self._committed_batches,
                "committed_tasks": self._committed_tasks,
                "failed_batches": self._failed_batches,
                "queued_now": self._q.qsize(),
            }

    # ─── Internals ────────────────────────────────────────────────

    def _drain_loop(self) -> None:
        while not self._stop_event.is_set():
            batch = self._collect_batch()
            if batch:
                self._commit_batch(batch)
            else:
                self._stop_event.wait(self._drain_interval)

    def _collect_batch(self) -> list[DBWriteTask]:
        batch: list[DBWriteTask] = []
        for _ in range(self._batch_size):
            try:
                t = self._q.get_nowait()
                batch.append(t)
            except queue.Empty:
                break
        return batch

    def _drain_remaining(self) -> None:
        while True:
            batch = self._collect_batch()
            if not batch:
                break
            self._commit_batch(batch)

    def _commit_batch(self, batch: list[DBWriteTask]) -> None:
        try:
            with self._session_factory() as session:
                for task in batch:
                    task.fn(session)
            with self._lock:
                self._committed_batches += 1
                self._committed_tasks += len(batch)
        except Exception:
            with self._lock:
                self._failed_batches += 1
            # Per policy: never raise from bg thread. Whole batch is lost on
            # commit failure (DB-level rollback). Caller's session_factory
            # should log; we just bump counter.
