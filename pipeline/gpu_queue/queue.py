"""
pipeline/gpu_queue/queue.py
Queue abstraction and pluggable offline storage (SQLite and JSON) for the GPU Queue.
"""
from abc import ABC, abstractmethod
import json
import os
import sqlite3
import tempfile
import threading
from typing import Any, Dict, List, Optional

from .models import (
    GPUJob,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    now_iso,
)


class BaseJobStore(ABC):
    """Abstract interface for job storage."""

    @abstractmethod
    def add(self, job: GPUJob) -> None:
        """Store a new job."""
        pass

    @abstractmethod
    def get(self, job_id: str) -> Optional[GPUJob]:
        """Retrieve a job by ID."""
        pass

    @abstractmethod
    def update(self, job: GPUJob) -> None:
        """Update an existing job."""
        pass

    @abstractmethod
    def list(self, status: Optional[str] = None) -> List[GPUJob]:
        """List all jobs, optionally filtered by status."""
        pass

    @abstractmethod
    def delete(self, job_id: str) -> bool:
        """Delete a job by ID."""
        pass

    @abstractmethod
    def clear(self, status: Optional[str] = None) -> int:
        """Remove jobs, optionally filtered by status. Return count removed."""
        pass


class SQLiteJobStore(BaseJobStore):
    """
    SQLite-backed job store.
    Zero external database dependencies; defaults to 'data/gpu_queue.db'.
    Supports ':memory:' for transient test suites.
    """

    def __init__(self, db_path: str = "data/gpu_queue.db"):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._memory_conn: Optional[sqlite3.Connection] = None

        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        else:
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.row_factory = sqlite3.Row

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._memory_conn is not None:
            self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS gpu_jobs (
                        id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        priority INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        error TEXT,
                        result TEXT
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_gpu_jobs_prio_status 
                    ON gpu_jobs (status, priority, created_at);
                """)
                conn.commit()
            finally:
                if self._memory_conn is None:
                    conn.close()

    def add(self, job: GPUJob) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                payload_json = json.dumps(job.payload)
                result_json = json.dumps(job.result) if job.result is not None else None
                conn.execute(
                    """
                    INSERT INTO gpu_jobs (
                        id, kind, model_id, payload, priority, status,
                        created_at, started_at, completed_at, error, result
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.id,
                        job.kind,
                        job.model_id,
                        payload_json,
                        job.priority,
                        job.status,
                        job.created_at,
                        job.started_at,
                        job.completed_at,
                        job.error,
                        result_json,
                    ),
                )
                conn.commit()
            finally:
                if self._memory_conn is None:
                    conn.close()

    def _row_to_job(self, row: sqlite3.Row) -> GPUJob:
        return GPUJob.from_dict({
            "id": row["id"],
            "kind": row["kind"],
            "model_id": row["model_id"],
            "payload": row["payload"],
            "priority": row["priority"],
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "error": row["error"],
            "result": row["result"],
        })

    def get(self, job_id: str) -> Optional[GPUJob]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute("SELECT * FROM gpu_jobs WHERE id = ?", (job_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return self._row_to_job(row)
            finally:
                if self._memory_conn is None:
                    conn.close()

    def update(self, job: GPUJob) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                payload_json = json.dumps(job.payload)
                result_json = json.dumps(job.result) if job.result is not None else None
                conn.execute(
                    """
                    UPDATE gpu_jobs SET
                        kind = ?,
                        model_id = ?,
                        payload = ?,
                        priority = ?,
                        status = ?,
                        created_at = ?,
                        started_at = ?,
                        completed_at = ?,
                        error = ?,
                        result = ?
                    WHERE id = ?
                    """,
                    (
                        job.kind,
                        job.model_id,
                        payload_json,
                        job.priority,
                        job.status,
                        job.created_at,
                        job.started_at,
                        job.completed_at,
                        job.error,
                        result_json,
                        job.id,
                    ),
                )
                conn.commit()
            finally:
                if self._memory_conn is None:
                    conn.close()

    def list(self, status: Optional[str] = None) -> List[GPUJob]:
        with self._lock:
            conn = self._get_connection()
            try:
                if status:
                    cur = conn.execute(
                        "SELECT * FROM gpu_jobs WHERE status = ? ORDER BY priority ASC, created_at ASC",
                        (status,),
                    )
                else:
                    cur = conn.execute(
                        "SELECT * FROM gpu_jobs ORDER BY priority ASC, created_at ASC"
                    )
                return [self._row_to_job(r) for r in cur.fetchall()]
            finally:
                if self._memory_conn is None:
                    conn.close()

    def delete(self, job_id: str) -> bool:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute("DELETE FROM gpu_jobs WHERE id = ?", (job_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                if self._memory_conn is None:
                    conn.close()

    def clear(self, status: Optional[str] = None) -> int:
        with self._lock:
            conn = self._get_connection()
            try:
                if status:
                    cur = conn.execute("DELETE FROM gpu_jobs WHERE status = ?", (status,))
                else:
                    cur = conn.execute("DELETE FROM gpu_jobs")
                conn.commit()
                return cur.rowcount
            finally:
                if self._memory_conn is None:
                    conn.close()


class JsonJobStore(BaseJobStore):
    """
    JSON file-backed job store.
    Simple and offline, atomically reads and writes a single JSON file.
    """

    def __init__(self, json_path: str = "data/gpu_queue.json"):
        self.json_path = json_path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(os.path.abspath(self.json_path)), exist_ok=True)
        if not os.path.exists(self.json_path):
            self._save_raw([])

    def _load_raw(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.json_path):
            return []
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "jobs" in data:
                    return data["jobs"]
                return []
        except Exception:
            return []

    def _save_raw(self, jobs_data: List[Dict[str, Any]]) -> None:
        dir_name = os.path.dirname(os.path.abspath(self.json_path))
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
            json.dump(jobs_data, tf, indent=2)
            temp_name = tf.name
        os.replace(temp_name, self.json_path)

    def add(self, job: GPUJob) -> None:
        with self._lock:
            jobs = self._load_raw()
            # Replace if already exists, else append
            jobs = [j for j in jobs if j.get("id") != job.id]
            jobs.append(job.to_dict())
            self._save_raw(jobs)

    def get(self, job_id: str) -> Optional[GPUJob]:
        with self._lock:
            jobs = self._load_raw()
            for j in jobs:
                if j.get("id") == job_id:
                    return GPUJob.from_dict(j)
            return None

    def update(self, job: GPUJob) -> None:
        with self._lock:
            jobs = self._load_raw()
            updated = False
            for idx, item in enumerate(jobs):
                if item.get("id") == job.id:
                    jobs[idx] = job.to_dict()
                    updated = True
                    break
            if not updated:
                jobs.append(job.to_dict())
            self._save_raw(jobs)

    def list(self, status: Optional[str] = None) -> List[GPUJob]:
        with self._lock:
            jobs = self._load_raw()
            job_objs = [GPUJob.from_dict(j) for j in jobs]
            if status:
                job_objs = [j for j in job_objs if j.status == status]
            job_objs.sort(key=lambda j: (j.priority, j.created_at))
            return job_objs

    def delete(self, job_id: str) -> bool:
        with self._lock:
            jobs = self._load_raw()
            orig_len = len(jobs)
            jobs = [j for j in jobs if j.get("id") != job_id]
            if len(jobs) != orig_len:
                self._save_raw(jobs)
                return True
            return False

    def clear(self, status: Optional[str] = None) -> int:
        with self._lock:
            jobs = self._load_raw()
            if status:
                remaining = [j for j in jobs if j.get("status") != status]
                cleared_count = len(jobs) - len(remaining)
                self._save_raw(remaining)
                return cleared_count
            else:
                cleared_count = len(jobs)
                self._save_raw([])
                return cleared_count


class GPUQueue:
    """
    Queue manager handling prioritization and job retrieval.
    Prioritizes Tier 1 text > Tier 2 headshots > Tier 3 audio.
    Supports model batching when an active model is loaded.
    """

    def __init__(self, store: Optional[BaseJobStore] = None):
        self.store = store if store is not None else SQLiteJobStore()

    def enqueue(self, job: GPUJob) -> str:
        """Enqueue a GPU job. Returns job ID."""
        self.store.add(job)
        return job.id

    def peek_next_job(self, model_id: Optional[str] = None) -> Optional[GPUJob]:
        """
        Inspect the next job without changing its status.
        Selects highest priority (lowest integer value).
        If multiple jobs share the top priority tier and model_id is specified,
        prefers pending jobs matching model_id to avoid unnecessary model reload.
        """
        pending = self.store.list(status=STATUS_PENDING)
        return self._select_next_job(pending, model_id=model_id)

    def get_next_job(
        self, model_id: Optional[str] = None, auto_start: bool = True
    ) -> Optional[GPUJob]:
        """
        Retrieve and optionally transition the next eligible job to 'running'.
        """
        pending = self.store.list(status=STATUS_PENDING)
        job = self._select_next_job(pending, model_id=model_id)
        if job and auto_start:
            job.status = STATUS_RUNNING
            job.started_at = now_iso()
            self.store.update(job)
        return job

    def mark_completed(self, job_id: str, result: Any = None) -> Optional[GPUJob]:
        """Mark job completed with optional result."""
        job = self.store.get(job_id)
        if job:
            job.status = STATUS_COMPLETED
            job.completed_at = now_iso()
            job.result = result
            self.store.update(job)
        return job

    def mark_failed(self, job_id: str, error: str) -> Optional[GPUJob]:
        """Mark job failed with error description."""
        job = self.store.get(job_id)
        if job:
            job.status = STATUS_FAILED
            job.completed_at = now_iso()
            job.error = str(error)
            self.store.update(job)
        return job

    def get_job(self, job_id: str) -> Optional[GPUJob]:
        """Retrieve job by ID."""
        return self.store.get(job_id)

    def list_jobs(self, status: Optional[str] = None) -> List[GPUJob]:
        """List jobs, optionally filtered by status."""
        return self.store.list(status=status)

    def clear(self, status: Optional[str] = None) -> int:
        """Clear jobs from queue."""
        return self.store.clear(status=status)

    def _select_next_job(
        self, pending_jobs: List[GPUJob], model_id: Optional[str] = None
    ) -> Optional[GPUJob]:
        """
        Selection algorithm:
        1. Find minimum priority tier amongst all pending jobs (e.g. 1 is highest priority).
        2. Within that tier:
           - If model_id is active, pick oldest job using model_id (model batching).
           - Otherwise, pick oldest job in the tier.
        """
        if not pending_jobs:
            return None

        min_prio = min(j.priority for j in pending_jobs)
        tier_jobs = [j for j in pending_jobs if j.priority == min_prio]

        if model_id:
            matching_model = [j for j in tier_jobs if j.model_id == model_id]
            if matching_model:
                matching_model.sort(key=lambda j: j.created_at)
                return matching_model[0]

        tier_jobs.sort(key=lambda j: j.created_at)
        return tier_jobs[0]


def enqueue(job: GPUJob, store: Optional[BaseJobStore] = None) -> str:
    """Convenience helper to enqueue a job."""
    q = GPUQueue(store=store)
    return q.enqueue(job)
