"""Phase 1.5 v3 async IO queues — DiagQueue + DBWriteQueue.

Standalone modules, NOT wired into orchestrator yet (avoid double-rail per
§11.3 #2/#3). Wire-in is Step 7 of §11.4 9-step execution sequence; happens
after Win 端 verify completes.

Purpose: replace inline `diag.emit(...)` + `session.commit()` calls with
queue-then-bg-write pattern so main tick loop is not blocked by DB IO
(currently ~700ms/tick per Phase 1.5 v3 §2.6 estimate).
"""

from .diag_queue import DiagEvent, DiagQueue
from .db_queue import DBWriteQueue, DBWriteTask

__all__ = [
    "DiagEvent",
    "DiagQueue",
    "DBWriteTask",
    "DBWriteQueue",
]
