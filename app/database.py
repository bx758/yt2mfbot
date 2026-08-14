from __future__ import annotations

from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from .config import DATABASE_URL


def now() -> datetime:
    return datetime.now(timezone.utc)


def connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db() -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    is_allowed BOOLEAN NOT NULL DEFAULT TRUE,
                    is_banned BOOLEAN NOT NULL DEFAULT FALSE,
                    default_quality TEXT NOT NULL DEFAULT 'best',
                    default_destination TEXT NOT NULL DEFAULT 'ask',
                    subtitle_lang TEXT NOT NULL DEFAULT 'none',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    chat_id BIGINT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    format TEXT,
                    destination TEXT,
                    file_path TEXT,
                    size_mb DOUBLE PRECISION,
                    error TEXT,
                    worker_id TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_until TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT,
                    destination TEXT,
                    file_path TEXT,
                    size_mb DOUBLE PRECISION,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rate_events (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_worker_id ON jobs(worker_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_lease_until ON jobs(lease_until)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_history_user_id ON history(user_id, id DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_rate_events_user ON rate_events(user_id, created_at)")
        conn.commit()


def upsert_user(user_id: int, username: str = "", first_name: str = "") -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO users (user_id, username, first_name, created_at, last_seen)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_seen = NOW()
        """, (user_id, username or "", first_name or ""))
        conn.commit()


def get_user(user_id: int):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        return cur.fetchone()


def is_banned(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user and user["is_banned"])


def set_user_setting(user_id: int, key: str, value: str) -> None:
    allowed = {"default_quality", "default_destination", "subtitle_lang"}
    if key not in allowed:
        raise ValueError("Unsupported user setting")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE users SET {key} = %s, last_seen = NOW() WHERE user_id = %s", (value, user_id))
        conn.commit()


def create_job(user_id: int, chat_id: int, url: str, title: str | None = None) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO jobs (user_id, chat_id, url, title, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, 'queued', NOW(), NOW())
            RETURNING id
        """, (user_id, chat_id, url, title))
        job_id = cur.fetchone()["id"]
        conn.commit()
    return int(job_id)


def update_job(job_id: int, **values) -> None:
    allowed = {
        "status", "title", "format", "destination", "file_path", "size_mb",
        "error", "worker_id", "attempts", "lease_until",
    }
    values = {k: v for k, v in values.items() if k in allowed}
    if not values:
        return
    values["updated_at"] = now()
    if values.get("status") in {"completed", "failed", "cancelled"}:
        values["completed_at"] = now()
        values["lease_until"] = None
    columns = ", ".join(f"{key} = %s" for key in values)
    params = list(values.values()) + [job_id]
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE jobs SET {columns} WHERE id = %s", params)
        conn.commit()


def get_job(job_id: int):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        return cur.fetchone()


def get_job_file(job_id: int):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, title, status, file_path, size_mb FROM jobs WHERE id = %s", (job_id,))
        return cur.fetchone()


def get_downloaded_files():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, title, status, file_path, size_mb, updated_at
            FROM jobs WHERE file_path IS NOT NULL AND file_path != '' ORDER BY id DESC
        """)
        return cur.fetchall()


def clear_job_file(job_id: int) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE jobs SET file_path = NULL, size_mb = NULL, updated_at = NOW() WHERE id = %s", (job_id,))
        conn.commit()


def claim_job(job_id: int, worker_id: str, lease_seconds: int = 1800):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s FOR UPDATE", (job_id,))
        job = cur.fetchone()
        if not job:
            conn.rollback()
            return None
        # A worker may reclaim a stale in-progress job; queued/retry jobs are normal.
        eligible = job["status"] in {"queued", "retry"}
        stale = (
            job["status"] in {"downloading", "compressing", "uploading"}
            and job["lease_until"] is not None
            and job["lease_until"] < now()
        )
        if not eligible and not stale:
            conn.rollback()
            return None
        cur.execute("""
            UPDATE jobs SET
                status = 'downloading', worker_id = %s, attempts = attempts + 1,
                lease_until = NOW() + (%s * INTERVAL '1 second'),
                updated_at = NOW(), error = NULL
            WHERE id = %s RETURNING *
        """, (worker_id, lease_seconds, job_id))
        claimed = cur.fetchone()
        conn.commit()
        return claimed


def claim_next_job(worker_id: str, lease_seconds: int = 1800):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT * FROM jobs
            WHERE status IN ('queued', 'retry')
               OR (status IN ('downloading','compressing','uploading')
                   AND lease_until IS NOT NULL AND lease_until < NOW())
            ORDER BY id ASC FOR UPDATE SKIP LOCKED LIMIT 1
        """)
        job = cur.fetchone()
        if not job:
            conn.rollback()
            return None
        cur.execute("""
            UPDATE jobs SET
                status = 'downloading', worker_id = %s, attempts = attempts + 1,
                lease_until = NOW() + (%s * INTERVAL '1 second'),
                updated_at = NOW(), error = NULL
            WHERE id = %s RETURNING *
        """, (worker_id, lease_seconds, job["id"]))
        claimed = cur.fetchone()
        conn.commit()
        return claimed


def heartbeat_job(job_id: int, worker_id: str, lease_seconds: int = 1800) -> bool:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE jobs SET lease_until = NOW() + (%s * INTERVAL '1 second'), updated_at = NOW()
            WHERE id = %s AND worker_id = %s AND status NOT IN ('completed','failed','cancelled')
        """, (lease_seconds, job_id, worker_id))
        changed = cur.rowcount == 1
        conn.commit()
        return changed


def release_job(job_id: int, worker_id: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE jobs SET status = 'retry', worker_id = NULL, lease_until = NULL, updated_at = NOW()
            WHERE id = %s AND worker_id = %s
        """, (job_id, worker_id))
        conn.commit()


def complete_job(job_id: int, worker_id: str, file_path: str | None = None, size_mb: float | None = None):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE jobs SET status='completed', file_path=COALESCE(%s,file_path),
                size_mb=COALESCE(%s,size_mb), lease_until=NULL, completed_at=NOW(), updated_at=NOW()
            WHERE id=%s AND worker_id=%s AND status NOT IN ('completed','cancelled')
            RETURNING *
        """, (file_path, size_mb, job_id, worker_id))
        job = cur.fetchone()
        conn.commit()
        return job


def fail_job(job_id: int, worker_id: str, error: str, retry: bool = True) -> None:
    status = "retry" if retry else "failed"
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE jobs SET status=%s, error=%s, worker_id=NULL, lease_until=NULL,
                updated_at=NOW(), completed_at=CASE WHEN %s='failed' THEN NOW() ELSE NULL END
            WHERE id=%s AND worker_id=%s
        """, (status, error[-5000:], status, job_id, worker_id))
        conn.commit()


def cancel_job(job_id: int) -> bool:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE jobs SET status='cancelled', worker_id=NULL, lease_until=NULL,
                updated_at=NOW(), completed_at=NOW()
            WHERE id=%s AND status NOT IN ('completed','failed','cancelled')
        """, (job_id,))
        changed = cur.rowcount == 1
        conn.commit()
        return changed


def active_jobs_for_user(user_id: int) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) AS count FROM jobs
            WHERE user_id=%s AND status IN ('queued','downloading','compressing','uploading','retry')
        """, (user_id,))
        return int(cur.fetchone()["count"])


def add_history(user_id: int, url: str, title: str | None, destination: str, file_path: str, size_mb: float) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO history (user_id,url,title,destination,file_path,size_mb,created_at)
            VALUES (%s,%s,%s,%s,%s,%s,NOW())
        """, (user_id,url,title,destination,file_path,size_mb))
        conn.commit()


def get_history(user_id: int, limit: int = 10):
    limit = max(1, min(int(limit), 100))
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM history WHERE user_id=%s ORDER BY id DESC LIMIT %s", (user_id, limit))
        return cur.fetchall()


def rate_ok(user_id: int, limit: int) -> bool:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (int(user_id),))
        cur.execute("SELECT COUNT(*) AS count FROM rate_events WHERE user_id=%s AND created_at >= NOW()-INTERVAL '1 hour'", (user_id,))
        if int(cur.fetchone()["count"]) >= limit:
            conn.rollback()
            return False
        cur.execute("INSERT INTO rate_events (user_id,created_at) VALUES (%s,NOW())", (user_id,))
        conn.commit()
        return True


def stats() -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM users")
        users = int(cur.fetchone()["count"])
        cur.execute("SELECT COUNT(*) AS count FROM jobs")
        jobs = int(cur.fetchone()["count"])
        cur.execute("SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','downloading','compressing','uploading','retry')")
        active = int(cur.fetchone()["count"])
        cur.execute("SELECT COUNT(*) AS count FROM history")
        history = int(cur.fetchone()["count"])
    return {"users": users, "jobs": jobs, "active": active, "history": history}


def get_active_file_paths() -> set[str]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT file_path FROM jobs
            WHERE status IN ('queued','downloading','compressing','uploading','retry')
              AND file_path IS NOT NULL AND file_path != ''
        """)
        return {str(row["file_path"]) for row in cur.fetchall() if row["file_path"]}


def recover_expired_jobs():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE jobs SET status='retry', worker_id=NULL, lease_until=NULL,
                error='Worker lease expired', updated_at=NOW()
            WHERE status IN ('downloading','compressing','uploading')
              AND lease_until IS NOT NULL AND lease_until < NOW()
            RETURNING id
        """)
        rows = cur.fetchall()
        conn.commit()
    return [int(row["id"]) for row in rows]
