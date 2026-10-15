"""Transactional shared-resource holds and explicit compensation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class AllocationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise ValueError("allocation database missing; seed first")

    @classmethod
    def seed(cls, path: str | Path, fixture: str | Path) -> "AllocationStore":
        db = Path(path).resolve()
        if db.exists():
            raise ValueError("seed destination already exists")
        db.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(Path(fixture).read_text())
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE resources (id TEXT PRIMARY KEY, kind TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE requests (id TEXT PRIMARY KEY, needs TEXT NOT NULL, status TEXT NOT NULL,
                  fail_commit INTEGER NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE holds (resource_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                  status TEXT NOT NULL);
                CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
                  event TEXT NOT NULL, resource_id TEXT);
            """)
            for item in data["resources"]:
                conn.execute("INSERT INTO resources VALUES (?,?,1)", (item["id"], item["kind"]))
            for item in data["requests"]:
                conn.execute("INSERT INTO requests VALUES (?,?,?,?,1)",
                             (item["id"], json.dumps(item["needs"]), "pending", int(item["fail_commit"])))
        return cls(db)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _request(conn, request_id: str) -> dict:
        row = conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise ValueError("unknown request")
        value = dict(row); value["needs"] = json.loads(value["needs"])
        return value

    def inspect(self, request_id: str) -> dict:
        with self.connect() as conn:
            request = self._request(conn, request_id)
            resources = [dict(row) for row in conn.execute("SELECT id,kind,revision FROM resources ORDER BY id")]
            holds = [dict(row) for row in conn.execute("SELECT resource_id,request_id,status FROM holds ORDER BY resource_id")]
            return {"request": request, "resources": resources, "holds": holds}

    def reserve(self, request_id: str, resource_id: str, expected_revision: int) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            request = self._request(conn, request_id)
            if request["status"] not in ("pending", "holding"):
                raise ValueError("request not accepting holds")
            if resource_id not in request["needs"]:
                raise ValueError("resource not required by request")
            resource = conn.execute("SELECT * FROM resources WHERE id=?", (resource_id,)).fetchone()
            if not resource:
                raise ValueError("unknown resource")
            if resource["revision"] != expected_revision:
                raise ValueError("stale resource revision")
            if conn.execute("SELECT 1 FROM holds WHERE resource_id=?", (resource_id,)).fetchone():
                raise ValueError("resource unavailable")
            conn.execute("INSERT INTO holds VALUES (?,?,?)", (resource_id, request_id, "pending"))
            conn.execute("UPDATE resources SET revision=revision+1 WHERE id=?", (resource_id,))
            conn.execute("UPDATE requests SET status='holding',revision=revision+1 WHERE id=?", (request_id,))
            conn.execute("INSERT INTO audit (request_id,event,resource_id) VALUES (?,?,?)",
                         (request_id, "reserved", resource_id))
            conn.commit()
            return {"request_id": request_id, "resource_id": resource_id,
                    "resource_revision": resource["revision"] + 1}

    def commit(self, request_id: str) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            request = self._request(conn, request_id)
            if request["status"] != "holding":
                raise ValueError("request has no pending allocation")
            held = {row["resource_id"] for row in conn.execute(
                "SELECT resource_id FROM holds WHERE request_id=? AND status='pending'", (request_id,))}
            if held != set(request["needs"]):
                raise ValueError("missing required resources")
            if request["fail_commit"]:
                conn.execute("UPDATE requests SET status='needs_compensation',revision=revision+1 WHERE id=?", (request_id,))
                conn.execute("INSERT INTO audit (request_id,event) VALUES (?,?)", (request_id, "commit_failed"))
                conn.commit()
                return {"request_id": request_id, "error": "commit_failed", "needs_compensation": True}
            conn.execute("UPDATE holds SET status='committed' WHERE request_id=?", (request_id,))
            conn.execute("UPDATE requests SET status='confirmed',revision=revision+1 WHERE id=?", (request_id,))
            conn.execute("INSERT INTO audit (request_id,event) VALUES (?,?)", (request_id, "confirmed"))
            conn.commit()
            return {"request_id": request_id, "status": "confirmed", "resources": sorted(held)}

    def compensate(self, request_id: str) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            request = self._request(conn, request_id)
            if request["status"] not in ("holding", "needs_compensation"):
                raise ValueError("nothing compensatable")
            held = [row["resource_id"] for row in conn.execute(
                "SELECT resource_id FROM holds WHERE request_id=? AND status='pending'", (request_id,))]
            for resource_id in held:
                conn.execute("DELETE FROM holds WHERE resource_id=? AND request_id=?", (resource_id, request_id))
                conn.execute("UPDATE resources SET revision=revision+1 WHERE id=?", (resource_id,))
            conn.execute("UPDATE requests SET status='compensated',revision=revision+1 WHERE id=?", (request_id,))
            conn.execute("INSERT INTO audit (request_id,event) VALUES (?,?)", (request_id, "compensated"))
            conn.commit()
            return {"request_id": request_id, "status": "compensated", "released": sorted(held)}

    def audit(self, request_id: str) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT event,resource_id FROM audit WHERE request_id=? ORDER BY id", (request_id,))]
