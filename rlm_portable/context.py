"""Lossless source snapshots, bounded work items, and evidence/coverage bookkeeping.

Budgets are UTF-8 bytes, deliberately not model-token estimates. The host owns
model execution. Workers exchange pointers and write independently through SQLite.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pieces(text: str, limit: int):
    data = text.encode("utf-8")
    while data:
        part = data[:limit]
        try:
            value = part.decode("utf-8")
        except UnicodeDecodeError as exc:
            part = part[: exc.start]
            value = part.decode("utf-8")
        if not part:
            raise ValueError("Byte budget must fit one Unicode character")
        yield value
        data = data[len(part) :]


class Run:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.path = self.root / "run.sqlite3"

    @contextmanager
    def db(self):
        if not self.path.exists():
            raise ValueError("Initialize a run with init --query first")
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            # Serialize short state transitions; never hold this over model/network calls.
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def init(self, query: str, chunk_bytes: int = 16000):
        if not query.strip() or not 32 <= chunk_bytes <= 1_000_000:
            raise ValueError("Provide a query and chunk-bytes between 32 and 1000000")
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources (
                  id TEXT PRIMARY KEY, pointer TEXT UNIQUE, state TEXT, sha256 TEXT,
                  bytes INTEGER, error TEXT);
                CREATE TABLE IF NOT EXISTS chunks (
                  id TEXT PRIMARY KEY, source_id TEXT, path TEXT, sha256 TEXT,
                  start_byte INTEGER, end_byte INTEGER);
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY, parent TEXT, state TEXT, budget INTEGER,
                  result TEXT, depth INTEGER);
                CREATE TABLE IF NOT EXISTS assignments (
                  task_id TEXT, chunk_id TEXT, position INTEGER,
                  PRIMARY KEY(task_id, chunk_id));
            """)
        with self.db() as db:
            old = dict(db.execute("SELECT key, value FROM meta"))
            if old:
                if old["query"] != query or int(old["chunk_bytes"]) != chunk_bytes:
                    raise ValueError(
                        "Run already initialized with different settings; use a new directory"
                    )
            else:
                db.executemany(
                    "INSERT INTO meta VALUES (?,?)",
                    [
                        ("schema", "1"),
                        ("query", query),
                        ("chunk_bytes", str(chunk_bytes)),
                        ("planned", "0"),
                        ("run_id", uuid.uuid4().hex),
                    ],
                )
        return self.status()

    @staticmethod
    def _meta(db):
        return dict(db.execute("SELECT key, value FROM meta"))

    def _chunk(self, db, source: str, start: int, text: str):
        data = text.encode("utf-8")
        sha = digest(data)
        chunk_id = digest(f"{source}:{start}:{sha}".encode())[:24]
        relative = f"chunks/{chunk_id}.txt"
        path = self.root / relative
        path.parent.mkdir(exist_ok=True)
        # IDs are content-addressed; a worker can never overwrite another source span.
        if path.exists() and digest(path.read_bytes()) != sha:
            raise ValueError(f"Corrupted snapshot: {chunk_id}")
        if not path.exists():
            tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        db.execute(
            "INSERT OR IGNORE INTO chunks VALUES (?,?,?,?,?,?)",
            (chunk_id, source, relative, sha, start, start + len(data)),
        )
        return chunk_id

    def add_file(self, path: str | Path, pointer: str | None = None, state: str = "ready"):
        path = Path(path).resolve()
        pointer = pointer or path.as_uri()
        source_id = digest(pointer.encode())[:24]
        try:
            with self.db() as db:
                if self._meta(db)["planned"] == "1":
                    raise ValueError(
                        "Sources are frozen after plan; use a new run for changed inputs"
                    )
                if db.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone():
                    raise ValueError("Source already recorded; use a new run for changed inputs")
                hasher, offset = hashlib.sha256(), 0
                size = int(self._meta(db)["chunk_bytes"])
                with path.open(encoding="utf-8", newline="") as stream:
                    while block := stream.read(size):
                        if "\x00" in block:
                            raise ValueError(
                                "Binary input: convert it to text with the host document tools"
                            )
                        for text in pieces(block, size):
                            data = text.encode("utf-8")
                            self._chunk(db, source_id, offset, text)
                            hasher.update(data)
                            offset += len(data)
                db.execute(
                    "INSERT INTO sources VALUES (?,?,?,?,?,?)",
                    (
                        source_id,
                        pointer,
                        state,
                        hasher.hexdigest(),
                        offset,
                        "Source was truncated during acquisition" if state == "truncated" else None,
                    ),
                )
        except (OSError, UnicodeError) as exc:
            self.unavailable(pointer, str(exc))
            raise ValueError(f"Cannot ingest {pointer}: {exc}") from exc
        except ValueError as exc:
            # Keep conversion failures visible; do not mutate a frozen/duplicate source.
            if str(exc).startswith("Binary input:"):
                self.unavailable(pointer, str(exc))
            raise
        return source_id

    def unavailable(self, pointer: str, reason: str, state: str = "unavailable"):
        source_id = digest(pointer.encode())[:24]
        with self.db() as db:
            if self._meta(db)["planned"] == "1":
                raise ValueError("Sources are frozen after plan")
            db.execute(
                "INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?)",
                (source_id, pointer, state, None, 0, reason),
            )
        return source_id

    def add_web(self, directory: str | Path):
        directory = Path(directory).resolve()
        entries = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        for entry in entries:
            pointer = entry["url"]
            if not entry.get("ok") or not entry.get("file"):
                self.unavailable(pointer, entry.get("error") or "Login or browser access required")
                continue
            path = (directory / entry["file"]).resolve()
            if not path.is_relative_to(directory):
                raise ValueError("Web index file escapes its run directory")
            self.add_file(
                path, pointer=pointer, state="truncated" if entry.get("truncated") else "ready"
            )
        return self.status()

    def _create_tasks(self, db, chunks, budget: int, parent=None, depth=0):
        groups, group, used = [], [], 0
        for c in chunks:
            size = c["end_byte"] - c["start_byte"]
            if size > budget:
                text = self._verified(c)
                offset = c["start_byte"]
                for part in pieces(text, budget):
                    chunk_id = self._chunk(db, c["source_id"], offset, part)
                    child = db.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
                    if group:
                        groups.append(group)
                        group, used = [], 0
                    groups.append([child])
                    offset += len(part.encode("utf-8"))
                continue
            if group and used + size > budget:
                groups.append(group)
                group, used = [], 0
            group.append(c)
            used += size
        if group:
            groups.append(group)
        ids = []
        for group in groups:
            task_id = uuid.uuid4().hex[:16]
            db.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?)",
                (task_id, parent, "pending", budget, None, depth),
            )
            db.executemany(
                "INSERT INTO assignments VALUES (?,?,?)",
                [(task_id, c["id"], i) for i, c in enumerate(group)],
            )
            ids.append(task_id)
        return ids

    def plan(self, max_bytes: int = 64000):
        if not 32 <= max_bytes <= 1_000_000:
            raise ValueError("max-bytes must be between 32 and 1000000")
        with self.db() as db:
            if self._meta(db)["planned"] == "0":
                if not db.execute("SELECT 1 FROM sources").fetchone():
                    raise ValueError("Add at least one source before planning")
                chunks = db.execute("SELECT * FROM chunks ORDER BY source_id,start_byte").fetchall()
                self._create_tasks(db, chunks, max_bytes)
                db.execute("UPDATE meta SET value='1' WHERE key='planned'")
        return self.tasks()

    def _verified(self, chunk):
        path = (self.root / chunk["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Snapshot path escapes run directory")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"Missing snapshot {chunk['id']}") from exc
        if digest(data) != chunk["sha256"]:
            raise ValueError(f"Corrupted snapshot {chunk['id']}")
        return data.decode("utf-8")

    def _task(self, db, task_id):
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown task: {task_id}")
        task = dict(row)
        task["query"] = self._meta(db)["query"]
        task["run_dir"] = str(self.root)
        chunks = db.execute(
            """SELECT c.*, s.pointer FROM assignments a
            JOIN chunks c ON a.chunk_id=c.id JOIN sources s ON c.source_id=s.id
            WHERE a.task_id=? ORDER BY a.position""",
            (task_id,),
        ).fetchall()
        task["chunks"] = [
            dict(c, path=str(self.root / c["path"]), bytes=c["end_byte"] - c["start_byte"])
            for c in chunks
        ]
        task["bytes"] = sum(c["end_byte"] - c["start_byte"] for c in chunks)
        task["budget_unit"] = "utf8_bytes (not model tokens)"
        task["result"] = json.loads(task["result"]) if task["result"] else None
        return task

    def task(self, task_id):
        with self.db() as db:
            return self._task(db, task_id)

    def tasks(self, pending=False):
        with self.db() as db:
            states = "state IN ('pending','failed')" if pending else "state != 'split'"
            rows = db.execute(f"SELECT id FROM tasks WHERE {states} ORDER BY rowid").fetchall()
            return [self._task(db, row["id"]) for row in rows]

    def split(self, task_id: str, max_bytes: int):
        with self.db() as db:
            task = self._task(db, task_id)
            if task["state"] not in ("pending", "failed"):
                raise ValueError("Only pending or failed tasks can be split")
            if not 32 <= max_bytes < task["bytes"] or task["depth"] >= 8:
                raise ValueError("Split must reduce bytes and stay within depth 8")
            chunks = db.execute(
                """SELECT c.* FROM assignments a JOIN chunks c
                ON c.id=a.chunk_id WHERE a.task_id=? ORDER BY a.position""",
                (task_id,),
            ).fetchall()
            ids = self._create_tasks(db, chunks, max_bytes, task_id, task["depth"] + 1)
            db.execute("UPDATE tasks SET state='split', result=NULL WHERE id=?", (task_id,))
        return [self.task(i) for i in ids]

    def read(self, chunk_id: str, start=0, limit=4000):
        if start < 0 or not 1 <= limit <= 16000:
            raise ValueError("Use nonnegative start and limit between 1 and 16000 characters")
        with self.db() as db:
            row = db.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
            if not row:
                raise ValueError("Unknown chunk")
            text = self._verified(row)
        end = min(len(text), start + limit)
        return {
            "chunk_id": chunk_id,
            "start": start,
            "text": text[start:end],
            "next_start": end if end < len(text) else None,
            "total_chars": len(text),
        }

    def submit(self, task_id: str, payload: dict):
        if not isinstance(payload, dict) or payload.get("status") not in ("complete", "failed"):
            raise ValueError("Result status must be complete or failed")
        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 4000:
            raise ValueError("Provide a nonempty summary of at most 4000 characters")
        reviewed, evidence = payload.get("reviewed_chunks"), payload.get("evidence")
        if not isinstance(reviewed, list) or not all(isinstance(c, str) for c in reviewed):
            raise ValueError("reviewed_chunks must be a list of chunk IDs")
        if not isinstance(evidence, list) or len(evidence) > 32:
            raise ValueError("evidence must be a list of at most 32 exact quotes")
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 32000:
            raise ValueError("Result exceeds 32000 bytes; shorten it or split the task")
        with self.db() as db:
            task = self._task(db, task_id)
            if task["state"] == "split":
                raise ValueError("Submit results for leaf tasks, not a split parent")
            assigned = {c["id"]: c for c in task["chunks"]}
            if len(set(reviewed)) != len(reviewed) or not set(reviewed) <= assigned.keys():
                raise ValueError("Reviewed chunks must be unique and assigned to this task")
            if payload["status"] == "complete" and set(reviewed) != assigned.keys():
                raise ValueError("Complete results must account for every assigned chunk")
            texts = {cid: self._verified(c) for cid, c in assigned.items()}
            for item in evidence:
                if not isinstance(item, dict):
                    raise ValueError("Each evidence item needs chunk_id and quote")
                cid, quote = item.get("chunk_id"), item.get("quote")
                if (
                    not isinstance(cid, str)
                    or cid not in reviewed
                    or not isinstance(quote, str)
                    or not 1 <= len(quote) <= 800
                ):
                    raise ValueError(
                        "Evidence must cite a reviewed chunk and a quote of 1–800 characters"
                    )
                if quote not in texts[cid]:
                    raise ValueError("Evidence quote is not present in its cited chunk")
            if task["state"] == "complete" and task["result"] != payload:
                raise ValueError("Completed task already has a different result")
            db.execute(
                "UPDATE tasks SET state=?, result=? WHERE id=?",
                (payload["status"], encoded, task_id),
            )
        return {"task_id": task_id, "status": payload["status"]}

    def status(self):
        with self.db() as db:
            meta = self._meta(db)
            sources = [dict(s) for s in db.execute("SELECT * FROM sources ORDER BY rowid")]
            counts = dict(db.execute("SELECT state, COUNT(*) FROM tasks GROUP BY state"))
            unavailable = [s for s in sources if s["state"] != "ready"]
            complete = (
                meta.get("planned") == "1"
                and bool(sources)
                and not unavailable
                and counts.get("pending", 0) == 0
                and counts.get("failed", 0) == 0
            )
            # Validate active leaf spans, rather than trusting worker completion counts.
            for source in sources:
                spans = db.execute(
                    """SELECT c.* FROM tasks t JOIN assignments a ON a.task_id=t.id
                    JOIN chunks c ON c.id=a.chunk_id WHERE t.state!='split' AND c.source_id=?
                    ORDER BY c.start_byte""",
                    (source["id"],),
                ).fetchall()
                position = 0
                for c in spans:
                    if c["start_byte"] != position:
                        complete = False
                    position = c["end_byte"]
                    if complete:
                        try:
                            self._verified(c)
                        except ValueError:
                            complete = False
                if position != source["bytes"]:
                    complete = False
            return {
                "query": meta.get("query"),
                "run_dir": str(self.root),
                "planned": meta.get("planned") == "1",
                "complete": complete,
                "source_count": len(sources),
                "source_bytes": sum(s["bytes"] for s in sources),
                "task_counts": counts,
                "unavailable_sources": unavailable,
                "coverage_note": "Completion records declared review and exact quotes; it does not prove semantic accuracy.",
            }

    def export(self):
        status = self.status()
        tasks = self.tasks()
        # Each result is separately addressable; avoid an unbounded synthesis dump.
        out = self.root / "results"
        out.mkdir(exist_ok=True)
        pointers = []
        for task in tasks:
            if task["result"]:
                path = out / f"{task['id']}.json"
                path.write_text(
                    json.dumps(
                        {
                            "task_id": task["id"],
                            "run_dir": str(self.root),
                            "sources": task["chunks"],
                            "result": task["result"],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                pointers.append({"task_id": task["id"], "path": str(path), "state": task["state"]})
        manifest = dict(status, results=pointers)
        path = self.root / "coverage.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest
