"""Small intent journal. Disk waits run off the control event loop."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from wrs_agent.schemas import TERMINAL, ActionStatus


class Journal:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="intent-journal")
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS actions "
                "(id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL)"
            )
            rows = db.execute("SELECT id, request, status FROM actions").fetchall()
            self.records = {}
            for action_id, request, status in rows:
                old = ActionStatus.model_validate_json(status)
                if old.state not in TERMINAL:
                    old = old.model_copy(
                        update={
                            "state": "UNKNOWN",
                            "reason": "environment_restarted",
                            "verification": "INCONCLUSIVE",
                            "sequence": old.sequence + 1,
                        }
                    )
                    db.execute(
                        "UPDATE actions SET status=? WHERE id=?",
                        (old.model_dump_json(), action_id),
                    )
                self.records[action_id] = (json.loads(request), old)

    async def save(self, request, status):
        def write():
            with sqlite3.connect(self.path, timeout=2) as db:
                db.execute(
                    "INSERT INTO actions VALUES (?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET status=excluded.status",
                    (
                        status.action_id,
                        json.dumps(request, sort_keys=True),
                        status.model_dump_json(),
                    ),
                )

        await asyncio.get_running_loop().run_in_executor(self.executor, write)

    def close(self):
        self.executor.shutdown(wait=True)
