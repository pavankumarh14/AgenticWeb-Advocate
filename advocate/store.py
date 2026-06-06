"""
Durable-ish Case store (SQLite).
================================

This is the SIMPLIFIED local stand-in for Azure Cosmos DB in the problem
statement. Every Case is persisted as a JSON blob keyed by case_id. After each
step of the resolution loop the orchestrator calls `save()`, so a Case survives
the process exiting — you can stop the program and `resume` later (a tiny taste
of the "durable, survives restarts" property the full system gets from Azure
Durable Functions).

Stdlib only: uses the built-in `sqlite3` module.
"""

import json
import os
import sqlite3
import time
from typing import List, Optional

from .models import Case


class CaseStore:
    def __init__(self, path: str = "advocate.db"):
        self.path = path
        # check_same_thread=False + WAL + a busy timeout let the web server use
        # one store per worker thread and poll concurrently without locking up.
        self._conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=8000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cases (
                case_id    TEXT PRIMARY KEY,
                status     TEXT,
                goal       TEXT,
                updated_at REAL,
                data       TEXT
            )
            """
        )
        self._conn.commit()

    def save(self, case: Case) -> None:
        case.updated_at = time.time()
        self._conn.execute(
            "REPLACE INTO cases (case_id, status, goal, updated_at, data) VALUES (?,?,?,?,?)",
            (case.case_id, case.status, case.goal, case.updated_at,
             json.dumps(case.to_dict())),
        )
        self._conn.commit()

    def get(self, case_id: str) -> Optional[Case]:
        row = self._conn.execute(
            "SELECT data FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        if not row:
            return None
        return Case.from_dict(json.loads(row[0]))

    def list(self) -> List[Case]:
        rows = self._conn.execute(
            "SELECT data FROM cases ORDER BY updated_at DESC"
        ).fetchall()
        return [Case.from_dict(json.loads(r[0])) for r in rows]

    def delete(self, case_id: str) -> None:
        self._conn.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
