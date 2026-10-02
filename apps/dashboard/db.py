from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | None = None) -> None:
        raw_path = path or os.getenv("DATABASE_PATH", "./data/autopost.db")
        self.path = Path(raw_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._init_lock, self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    hidemyacc_id TEXT NOT NULL UNIQUE,
                    target_url TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    version TEXT NOT NULL,
                    hostname TEXT,
                    capabilities TEXT NOT NULL DEFAULT '{}',
                    current_job_id TEXT,
                    last_seen TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    profile_id INTEGER NOT NULL REFERENCES profiles(id),
                    kind TEXT NOT NULL DEFAULT 'create_and_post',
                    title TEXT NOT NULL DEFAULT '',
                    caption TEXT NOT NULL,
                    source_image TEXT NOT NULL DEFAULT '',
                    music_source TEXT NOT NULL DEFAULT '',
                    duration_seconds INTEGER NOT NULL DEFAULT 12,
                    width INTEGER NOT NULL DEFAULT 1080,
                    height INTEGER NOT NULL DEFAULT 1350,
                    background_color TEXT NOT NULL DEFAULT '#101828',
                    scheduled_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    assigned_agent_id TEXT,
                    lease_until TEXT,
                    artifact_path TEXT,
                    error TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_queue
                ON jobs(status, scheduled_at);

                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS page_targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    facebook_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS page_run_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id INTEGER NOT NULL REFERENCES page_targets(id) ON DELETE CASCADE,
                    source_url TEXT NOT NULL,
                    article_title TEXT NOT NULL DEFAULT '',
                    video_path TEXT NOT NULL DEFAULT '',
                    post_url TEXT NOT NULL DEFAULT '',
                    post_status TEXT NOT NULL DEFAULT 'not_started',
                    comment_status TEXT NOT NULL DEFAULT 'not_started',
                    step TEXT NOT NULL DEFAULT 'queued',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_page_run_history_page
                ON page_run_history(page_id, id DESC);

                CREATE TABLE IF NOT EXISTS page_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    page_ids TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    results TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_page_schedules_active_profile
                ON page_schedules(profile_id) WHERE status IN ('pending', 'running');

                CREATE INDEX IF NOT EXISTS idx_page_schedules_due
                ON page_schedules(status, scheduled_at);

                CREATE TABLE IF NOT EXISTS sync_requests (
                    id TEXT PRIMARY KEY,
                    folder_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    assigned_agent_id TEXT,
                    lease_until TEXT,
                    found_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sync_requests_queue
                ON sync_requests(status, created_at);
                """
            )
            self._ensure_column(conn, "profiles", "folder_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "profiles", "facebook_account_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "profiles", "facebook_account_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "profiles", "source", "TEXT NOT NULL DEFAULT 'manual'")
            self._ensure_column(conn, "profiles", "last_synced_at", "TEXT")
            self._ensure_column(conn, "page_targets", "source_url", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "page_targets", "article_title", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "page_targets", "article_language", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "page_targets", "reel_description", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "page_targets", "video_content", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "page_targets", "video_path", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "page_targets", "workflow_status", "TEXT NOT NULL DEFAULT 'idle'")
            self._ensure_column(conn, "page_targets", "last_error", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "page_targets", "post_url", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "page_run_history", "article_language", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "page_run_history", "video_content", "TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def summary(self) -> dict[str, Any]:
        with self.connect() as conn:
            counts = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
                )
            }
            cutoff = iso(utcnow() - timedelta(seconds=45))
            online = conn.execute(
                "SELECT COUNT(*) FROM agents WHERE last_seen >= ?", (cutoff,)
            ).fetchone()[0]
            return {
                "queued": counts.get("queued", 0),
                "processing": counts.get("processing", 0),
                "completed": counts.get("completed", 0),
                "failed": counts.get("failed", 0),
                "online_agents": online,
                "total_profiles": conn.execute(
                    "SELECT COUNT(*) FROM profiles WHERE enabled = 1"
                ).fetchone()[0],
            }

    def list_profiles(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT p.*,
                    SUM(CASE WHEN j.status = 'completed' THEN 1 ELSE 0 END) AS posts_done,
                    SUM(CASE WHEN j.status IN ('queued','processing') THEN 1 ELSE 0 END) AS active_jobs,
                    (SELECT COUNT(*) FROM page_targets t WHERE t.profile_id = p.id) AS page_count
                   FROM profiles p LEFT JOIN jobs j ON j.profile_id = p.id
                   GROUP BY p.id ORDER BY p.name COLLATE NOCASE ASC"""
            ).fetchall()
            return [dict(row) for row in rows]

    def ensure_default_profiles(self, count: int = 10) -> int:
        """Create the convention-based FB_01..FB_N list without using Hidemyacc API."""
        count = min(max(int(count), 1), 100)
        now = iso()
        created = 0
        with self.connect() as conn:
            for number in range(1, count + 1):
                name = f"FB_{number:02d}"
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO profiles
                       (name, hidemyacc_id, target_url, enabled, created_at, updated_at,
                        folder_name, facebook_account_name, facebook_account_id, source)
                       VALUES (?, ?, 'https://www.facebook.com/', 1, ?, ?,
                               'ACC_AUTO_FB', '', '', 'default')""",
                    (name, name, now, now),
                )
                created += cursor.rowcount
        return created

    def create_sync_request(self, folder_name: str) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        now = iso()
        with self.connect() as conn:
            current = conn.execute(
                "SELECT * FROM sync_requests WHERE folder_name = ? AND status IN ('queued','processing')",
                (folder_name,),
            ).fetchone()
            if current:
                return dict(current)
            conn.execute(
                """INSERT INTO sync_requests
                   (id, folder_name, status, created_at, updated_at)
                   VALUES (?, ?, 'queued', ?, ?)""",
                (request_id, folder_name, now, now),
            )
            return dict(conn.execute("SELECT * FROM sync_requests WHERE id = ?", (request_id,)).fetchone())

    def list_sync_requests(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM sync_requests ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            ]

    def claim_sync_request(self, agent_id: str) -> dict[str, Any] | None:
        now = iso()
        lease = iso(utcnow() + timedelta(minutes=5))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """UPDATE sync_requests SET status = 'queued', assigned_agent_id = NULL,
                       lease_until = NULL, updated_at = ?
                       WHERE status = 'processing' AND lease_until IS NOT NULL AND lease_until < ?""",
                    (now, now),
                )
                row = conn.execute(
                    "SELECT * FROM sync_requests WHERE status = 'queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
                if not row:
                    conn.execute("COMMIT")
                    return None
                conn.execute(
                    """UPDATE sync_requests SET status = 'processing', assigned_agent_id = ?,
                       lease_until = ?, updated_at = ? WHERE id = ?""",
                    (agent_id, lease, now, row["id"]),
                )
                conn.execute("COMMIT")
                return dict(
                    conn.execute("SELECT * FROM sync_requests WHERE id = ?", (row["id"],)).fetchone()
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def finish_sync_request(
        self,
        request_id: str,
        agent_id: str,
        status: str,
        profiles: list[dict[str, Any]],
        error: str | None = None,
    ) -> bool:
        if status not in {"completed", "failed"}:
            raise ValueError("Trạng thái đồng bộ không hợp lệ")
        now = iso()
        with self.connect() as conn:
            owned = conn.execute(
                """SELECT folder_name FROM sync_requests
                   WHERE id = ? AND assigned_agent_id = ? AND status = 'processing'""",
                (request_id, agent_id),
            ).fetchone()
            if not owned:
                return False
            if status == "completed":
                for profile in profiles:
                    hidemyacc_id = str(profile.get("hidemyacc_id", "")).strip()
                    name = str(profile.get("name", "")).strip()
                    if not hidemyacc_id or not name:
                        continue
                    conn.execute(
                        """INSERT INTO profiles
                           (name, hidemyacc_id, target_url, enabled, created_at, updated_at,
                            folder_name, source, last_synced_at)
                           VALUES (?, ?, 'https://www.facebook.com/', 1, ?, ?, ?, 'hidemyacc', ?)
                           ON CONFLICT(hidemyacc_id) DO UPDATE SET
                           name = excluded.name, folder_name = excluded.folder_name,
                           source = 'hidemyacc', enabled = 1,
                           last_synced_at = excluded.last_synced_at,
                           updated_at = excluded.updated_at""",
                        (name, hidemyacc_id, now, now, owned["folder_name"], now),
                    )
            conn.execute(
                """UPDATE sync_requests SET status = ?, found_count = ?, error = ?,
                   lease_until = NULL, updated_at = ? WHERE id = ?""",
                (status, len(profiles) if status == "completed" else 0, error, now, request_id),
            )
            return True

    def list_page_targets(self, profile_id: int | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            where = "WHERE t.profile_id = ?" if profile_id is not None else ""
            params = (profile_id,) if profile_id is not None else ()
            rows = conn.execute(
                f"""SELECT t.*, p.name AS profile_name,
                   EXISTS(SELECT 1 FROM page_run_history h WHERE h.page_id = t.id
                          AND h.source_url = t.source_url AND t.source_url != ''
                          AND h.post_status IN ('submitted', 'published')
                          AND h.comment_status != 'published') AS needs_comment_retry
                   FROM page_targets t LEFT JOIN profiles p ON p.id = t.profile_id
                   {where}
                   ORDER BY t.name COLLATE NOCASE ASC""",
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def ensure_page_targets(self, profile_name: str, targets: list[dict[str, str]]) -> int:
        now = iso()
        with self.connect() as conn:
            profile = conn.execute(
                "SELECT id FROM profiles WHERE name = ?", (profile_name,)
            ).fetchone()
            if not profile:
                raise ValueError(f"Profile {profile_name} không tồn tại")
            for target in targets:
                conn.execute(
                    """INSERT INTO page_targets
                       (facebook_id, name, url, profile_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(facebook_id) DO UPDATE SET
                       name = excluded.name, url = excluded.url,
                       profile_id = excluded.profile_id, updated_at = excluded.updated_at""",
                    (target["facebook_id"], target["name"], target["url"], profile["id"], now, now),
                )
            return len(targets)

    def get_page_target(self, page_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT t.*, p.name AS profile_name, p.hidemyacc_id,
                   EXISTS(SELECT 1 FROM page_run_history h WHERE h.page_id = t.id
                          AND h.source_url = t.source_url AND t.source_url != ''
                          AND h.post_status IN ('submitted', 'published')
                          AND h.comment_status != 'published') AS needs_comment_retry
                   FROM page_targets t JOIN profiles p ON p.id = t.profile_id
                   WHERE t.id = ?""",
                (page_id,),
            ).fetchone()
            return self._dict(row)

    def update_page_workflow(self, page_id: int, **values: Any) -> dict[str, Any] | None:
        allowed = {"source_url", "article_title", "article_language", "reel_description",
                   "video_content", "video_path", "workflow_status", "last_error", "post_url"}
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return self.get_page_target(page_id)
        updates["updated_at"] = iso()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE page_targets SET {assignments} WHERE id = ?",
                (*updates.values(), page_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_page_target(page_id)

    def bulk_save_page_sources(self, profile_id: int, rows: list[dict[str, Any]]) -> int:
        """Save validated Page inputs together, or leave every Page unchanged."""
        if not rows:
            raise ValueError("Không có Page nào để nhập")
        now = iso()
        with self.connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute(
                    "SELECT 1 FROM page_schedules WHERE profile_id = ? AND status IN ('pending', 'running')",
                    (profile_id,),
                ).fetchone():
                    raise ValueError("Profile đang có lịch hẹn; hãy huỷ lịch trước khi nhập hàng loạt")
                for row in rows:
                    target = conn.execute(
                        "SELECT id, source_url, reel_description, video_content FROM page_targets "
                        "WHERE id = ? AND profile_id = ? AND facebook_id = ?",
                        (row["id"], profile_id, row["facebook_id"]),
                    ).fetchone()
                    if not target:
                        raise ValueError(f"Page {row['facebook_id']} không còn thuộc profile này")
                    if conn.execute(
                        """SELECT 1 FROM page_run_history WHERE page_id = ? AND source_url = ?
                           AND source_url != '' AND post_status IN ('submitted', 'published')
                           AND comment_status != 'published' LIMIT 1""",
                        (target["id"], target["source_url"]),
                    ).fetchone():
                        raise ValueError(f"Page {row['facebook_id']} cần xử lý comment trong lịch sử trước")
                for row in rows:
                    conn.execute(
                        """UPDATE page_targets SET source_url = ?, reel_description = ?, video_content = ?,
                           article_title = ?, article_language = ?, video_path = '', post_url = '',
                           workflow_status = 'idle', last_error = '', updated_at = ? WHERE id = ?""",
                        (row["source_url"], row["reel_description"], row["video_content"],
                         row["reel_description"], row["article_language"], now, row["id"]),
                    )
                conn.commit()
                return len(rows)
            except Exception:
                conn.rollback()
                raise

    def create_page_run_history(self, page_id: int, source_url: str, article_title: str = "",
                                article_language: str = "", video_content: str = "") -> dict[str, Any]:
        now = iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO page_run_history
                   (page_id, source_url, article_title, article_language, video_content, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (page_id, source_url, article_title, article_language, video_content, now, now),
            )
            return dict(conn.execute(
                "SELECT * FROM page_run_history WHERE id = ?", (cursor.lastrowid,)
            ).fetchone())

    def update_page_run_history(self, history_id: int, **values: Any) -> dict[str, Any] | None:
        allowed = {"article_title", "article_language", "video_content", "video_path",
                   "post_url", "post_status", "comment_status", "step", "error"}
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return None
        updates["updated_at"] = iso()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self.connect() as conn:
            conn.execute(f"UPDATE page_run_history SET {assignments} WHERE id = ?",
                         (*updates.values(), history_id))
            row = conn.execute("SELECT * FROM page_run_history WHERE id = ?", (history_id,)).fetchone()
            return self._dict(row)

    def list_page_run_history(self, page_id: int, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        limit = min(max(limit, 1), 100)
        offset = max(offset, 0)
        with self.connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM page_run_history WHERE page_id = ?", (page_id,)
            ).fetchone()[0]
            rows = conn.execute(
                """SELECT * FROM page_run_history WHERE page_id = ?
                   ORDER BY id DESC LIMIT ? OFFSET ?""", (page_id, limit, offset)
            ).fetchall()
            return {"total": total, "items": [dict(row) for row in rows]}

    def get_page_run_history(self, history_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM page_run_history WHERE id = ?", (history_id,)).fetchone()
            return self._dict(row)

    @staticmethod
    def _schedule_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        result = dict(row)
        result["page_ids"] = json.loads(result["page_ids"])
        result["results"] = json.loads(result["results"])
        return result

    def create_page_schedule(self, profile_id: int, page_ids: list[int], scheduled_at: str) -> dict[str, Any]:
        if not page_ids or len(set(page_ids)) != len(page_ids):
            raise ValueError("Lịch đăng cần ít nhất một Page hợp lệ")
        now = iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            profile = conn.execute("SELECT id FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            if not profile:
                raise ValueError("Không tìm thấy profile")
            active = conn.execute(
                "SELECT id FROM page_schedules WHERE profile_id = ? AND status IN ('pending', 'running')",
                (profile_id,),
            ).fetchone()
            if active:
                raise ValueError("Profile đã có lịch đăng đang chờ hoặc đang chạy")
            rows = conn.execute(
                f"SELECT id, source_url, reel_description, video_content FROM page_targets "
                f"WHERE profile_id = ? AND id IN ({','.join('?' for _ in page_ids)})",
                (profile_id, *page_ids),
            ).fetchall()
            if len(rows) != len(page_ids) or any(
                not all(row[key] for key in ("source_url", "reel_description", "video_content"))
                for row in rows
            ):
                raise ValueError("Một Page chưa lưu đủ nội dung để lên lịch")
            if conn.execute(
                f"""SELECT 1 FROM page_run_history h JOIN page_targets t ON t.id = h.page_id
                    WHERE t.profile_id = ? AND t.id IN ({','.join('?' for _ in page_ids)})
                    AND h.source_url = t.source_url AND h.post_status IN ('submitted', 'published')
                    AND h.comment_status != 'published' LIMIT 1""",
                (profile_id, *page_ids),
            ).fetchone():
                raise ValueError("Có Page đã gửi bài nhưng comment chưa xong; xử lý trong lịch sử trước")
            cursor = conn.execute(
                """INSERT INTO page_schedules
                   (profile_id, page_ids, scheduled_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (profile_id, json.dumps(page_ids), scheduled_at, now, now),
            )
            row = conn.execute("SELECT * FROM page_schedules WHERE id = ?", (cursor.lastrowid,)).fetchone()
            conn.commit()
            return self._schedule_dict(row)

    def list_page_schedules(self, profile_id: int, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM page_schedules WHERE profile_id = ? ORDER BY id DESC LIMIT ?",
                (profile_id, min(max(limit, 1), 100)),
            ).fetchall()
            return [self._schedule_dict(row) for row in rows]

    def active_page_schedule(self, profile_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM page_schedules WHERE profile_id = ?
                   AND status IN ('pending', 'running') LIMIT 1""",
                (profile_id,),
            ).fetchone()
            return self._schedule_dict(row)

    def cancel_page_schedule(self, schedule_id: int, profile_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE page_schedules SET status = 'cancelled', updated_at = ?
                   WHERE id = ? AND profile_id = ? AND status = 'pending'""",
                (iso(), schedule_id, profile_id),
            )
            return cursor.rowcount == 1

    def claim_due_page_schedule(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM page_schedules WHERE status = 'pending' AND scheduled_at <= ?
                   ORDER BY scheduled_at, id LIMIT 1""",
                (iso(),),
            ).fetchone()
            if not row:
                conn.commit()
                return None
            conn.execute(
                "UPDATE page_schedules SET status = 'running', updated_at = ? WHERE id = ?",
                (iso(), row["id"]),
            )
            updated = conn.execute("SELECT * FROM page_schedules WHERE id = ?", (row["id"],)).fetchone()
            conn.commit()
            return self._schedule_dict(updated)

    def finish_page_schedule(self, schedule_id: int, status: str,
                             results: list[dict[str, Any]], error: str = "") -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("Trạng thái lịch đăng không hợp lệ")
        with self.connect() as conn:
            conn.execute(
                """UPDATE page_schedules SET status = ?, results = ?, error = ?, updated_at = ?
                   WHERE id = ? AND status = 'running'""",
                (status, json.dumps(results, ensure_ascii=False), error, iso(), schedule_id),
            )

    def recover_interrupted_page_schedules(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE page_schedules SET status = 'failed',
                   error = 'Dashboard đã dừng trong lúc chạy; kiểm tra lịch sử Page trước khi tạo lịch mới',
                   updated_at = ? WHERE status = 'running'""",
                (iso(),),
            )
            return cursor.rowcount

    def expire_missed_page_schedules(self, grace_seconds: int = 60) -> int:
        """Avoid an unexpected late post when the dashboard was offline at the chosen time."""
        cutoff = iso(utcnow() - timedelta(seconds=grace_seconds))
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE page_schedules SET status = 'failed',
                   error = 'Dashboard không chạy vào giờ hẹn; lịch đã quá hạn và không tự đăng bù',
                   updated_at = ? WHERE status = 'pending' AND scheduled_at < ?""",
                (iso(), cutoff),
            )
            return cursor.rowcount

    def clear_published_page_inputs(self, page_id: int, history_id: int) -> bool:
        """Archive the manual video text, then clear only inputs for a verified post."""
        with self.connect() as conn:
            target = conn.execute("SELECT * FROM page_targets WHERE id = ?", (page_id,)).fetchone()
            history = conn.execute(
                "SELECT * FROM page_run_history WHERE id = ? AND page_id = ?", (history_id, page_id)
            ).fetchone()
            if not target or not history or target["workflow_status"] != "published":
                return False
            if history["post_status"] != "published" or history["comment_status"] != "published":
                return False
            if not target["source_url"] or target["source_url"] != history["source_url"]:
                return False
            if target["reel_description"] and target["reel_description"] != history["article_title"]:
                return False
            if target["video_content"] and not history["video_content"]:
                conn.execute(
                    "UPDATE page_run_history SET video_content = ?, updated_at = ? WHERE id = ?",
                    (target["video_content"], iso(), history_id),
                )
            conn.execute(
                """UPDATE page_targets SET source_url = '', reel_description = '', video_content = '',
                   article_title = '', article_language = '', updated_at = ? WHERE id = ?""",
                (iso(), page_id),
            )
            return True

    def upsert_page_targets(
        self, targets: list[dict[str, Any]], profile_id: int | None = None
    ) -> list[dict[str, Any]]:
        now = iso()
        with self.connect() as conn:
            if profile_id is not None:
                exists = conn.execute("SELECT 1 FROM profiles WHERE id = ?", (profile_id,)).fetchone()
                if not exists:
                    raise ValueError("Hidemyacc profile không tồn tại")
            for target in targets:
                if not target.get("name"):
                    continue
                conn.execute(
                    """INSERT INTO page_targets
                       (facebook_id, name, url, profile_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(facebook_id) DO UPDATE SET
                       name = excluded.name, url = excluded.url,
                       profile_id = COALESCE(excluded.profile_id, page_targets.profile_id),
                       updated_at = excluded.updated_at""",
                    (
                        target["facebook_id"],
                        target["name"],
                        target["url"],
                        profile_id,
                        now,
                        now,
                    ),
                )
        return self.list_page_targets()

    def sync_page_targets(
        self, profile_id: int, targets: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Replace a profile's current Page membership without deleting workflow data."""
        now = iso()
        normalized: dict[str, dict[str, str]] = {}
        for target in targets:
            facebook_id = str(target.get("facebook_id", "")).strip()
            name = str(target.get("name", "")).strip()
            url = str(target.get("url", "")).strip()
            if facebook_id and name:
                normalized[facebook_id] = {
                    "facebook_id": facebook_id,
                    "name": name,
                    "url": url or f"https://www.facebook.com/profile.php?id={facebook_id}",
                }

        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM profiles WHERE id = ?", (profile_id,)).fetchone():
                raise ValueError("Hidemyacc profile không tồn tại")
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Detach stale rows instead of deleting them so generated videos,
                # source links and publish history remain recoverable.
                if normalized:
                    placeholders = ",".join("?" for _ in normalized)
                    conn.execute(
                        f"""UPDATE page_targets SET profile_id = NULL, updated_at = ?
                            WHERE profile_id = ? AND facebook_id NOT IN ({placeholders})""",
                        (now, profile_id, *normalized.keys()),
                    )
                else:
                    conn.execute(
                        "UPDATE page_targets SET profile_id = NULL, updated_at = ? WHERE profile_id = ?",
                        (now, profile_id),
                    )
                for target in normalized.values():
                    conn.execute(
                        """INSERT INTO page_targets
                           (facebook_id, name, url, profile_id, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(facebook_id) DO UPDATE SET
                           name = excluded.name, url = excluded.url,
                           profile_id = excluded.profile_id, updated_at = excluded.updated_at""",
                        (
                            target["facebook_id"], target["name"], target["url"],
                            profile_id, now, now,
                        ),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.list_page_targets(profile_id)

    def create_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        now = iso()
        with self.connect() as conn:
            try:
                cursor = conn.execute(
                    """INSERT INTO profiles
                       (name, hidemyacc_id, target_url, enabled, created_at, updated_at,
                        folder_name, facebook_account_name, facebook_account_id, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual')""",
                    (
                        data["name"].strip(),
                        data["hidemyacc_id"].strip(),
                        data["target_url"].strip(),
                        1 if data.get("enabled", True) else 0,
                        now,
                        now,
                        data.get("folder_name", "").strip(),
                        data.get("facebook_account_name", "").strip(),
                        data.get("facebook_account_id", "").strip(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("HideMyAcc Profile ID đã tồn tại") from exc
            return self._dict(
                conn.execute("SELECT * FROM profiles WHERE id = ?", (cursor.lastrowid,)).fetchone()
            ) or {}

    def update_profile(self, profile_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as conn:
            current = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            if not current:
                return None
            merged = dict(current)
            merged.update(data)
            try:
                conn.execute(
                    """UPDATE profiles SET name = ?, hidemyacc_id = ?, target_url = ?,
                       enabled = ?, folder_name = ?, facebook_account_name = ?,
                       facebook_account_id = ?, updated_at = ? WHERE id = ?""",
                    (
                        merged["name"].strip(),
                        merged["hidemyacc_id"].strip(),
                        merged["target_url"].strip(),
                        1 if merged.get("enabled", True) else 0,
                        str(merged.get("folder_name", "")).strip(),
                        str(merged.get("facebook_account_name", "")).strip(),
                        str(merged.get("facebook_account_id", "")).strip(),
                        iso(),
                        profile_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("HideMyAcc Profile ID đã tồn tại") from exc
            return self._dict(conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone())

    def list_jobs(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE j.status = ?"
            params.append(status)
        params.append(min(max(limit, 1), 500))
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT j.*, p.name AS profile_name, p.hidemyacc_id, p.target_url
                    FROM jobs j JOIN profiles p ON p.id = j.profile_id
                    {where}
                    ORDER BY j.created_at DESC LIMIT ?""",
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_job(self, job_id: str, include_events: bool = True) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT j.*, p.name AS profile_name, p.hidemyacc_id, p.target_url
                   FROM jobs j JOIN profiles p ON p.id = j.profile_id WHERE j.id = ?""",
                (job_id,),
            ).fetchone()
            result = self._dict(row)
            if result and include_events:
                result["events"] = [
                    dict(event)
                    for event in conn.execute(
                        "SELECT level, message, created_at FROM job_events WHERE job_id = ? ORDER BY id",
                        (job_id,),
                    ).fetchall()
                ]
            return result

    def create_job(self, data: dict[str, Any]) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = iso()
        with self.connect() as conn:
            profile = conn.execute(
                "SELECT id, enabled FROM profiles WHERE id = ?", (data["profile_id"],)
            ).fetchone()
            if not profile:
                raise ValueError("Profile không tồn tại")
            if not profile["enabled"]:
                raise ValueError("Profile đang bị tắt")
            conn.execute(
                """INSERT INTO jobs
                   (id, profile_id, kind, title, caption, source_image, music_source,
                    duration_seconds, width, height, background_color, scheduled_at,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (
                    job_id,
                    data["profile_id"],
                    data.get("kind", "create_and_post"),
                    data.get("title", "").strip(),
                    data.get("caption", "").strip(),
                    data.get("source_image", "").strip(),
                    data.get("music_source", "").strip(),
                    int(data.get("duration_seconds", 12)),
                    int(data.get("width", 1080)),
                    int(data.get("height", 1350)),
                    data.get("background_color", "#101828"),
                    data["scheduled_at"],
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO job_events (job_id, level, message, created_at) VALUES (?, 'info', ?, ?)",
                (job_id, "Đã tạo và đưa vào hàng chờ", now),
            )
        return self.get_job(job_id) or {}

    def cancel_job(self, job_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE jobs SET status = 'cancelled', updated_at = ?
                   WHERE id = ? AND status = 'queued'""",
                (iso(), job_id),
            )
            if cursor.rowcount:
                conn.execute(
                    "INSERT INTO job_events (job_id, level, message, created_at) VALUES (?, 'warning', ?, ?)",
                    (job_id, "Đã hủy từ dashboard", iso()),
                )
            return cursor.rowcount == 1

    def retry_job(self, job_id: str) -> bool:
        now = iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE jobs SET status = 'queued', assigned_agent_id = NULL,
                   lease_until = NULL, error = NULL, scheduled_at = ?, updated_at = ?
                   WHERE id = ? AND status IN ('failed','cancelled')""",
                (now, now, job_id),
            )
            if cursor.rowcount:
                conn.execute(
                    "INSERT INTO job_events (job_id, level, message, created_at) VALUES (?, 'info', ?, ?)",
                    (job_id, "Đã đưa lại vào hàng chờ", now),
                )
            return cursor.rowcount == 1

    def list_agents(self) -> list[dict[str, Any]]:
        cutoff = iso(utcnow() - timedelta(seconds=45))
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY last_seen DESC").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["online"] = item["last_seen"] >= cutoff
                try:
                    item["capabilities"] = json.loads(item["capabilities"])
                except json.JSONDecodeError:
                    item["capabilities"] = {}
                result.append(item)
            return result

    def upsert_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        now = iso()
        name = data["name"].strip()
        agent_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"hidemyacc-agent:{name}"))
        capabilities = json.dumps(data.get("capabilities", {}), ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO agents
                   (id, name, version, hostname, capabilities, current_job_id, last_seen, created_at)
                   VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET version = excluded.version,
                   hostname = excluded.hostname, capabilities = excluded.capabilities,
                   last_seen = excluded.last_seen""",
                (
                    agent_id,
                    name,
                    data.get("version", "unknown"),
                    data.get("hostname", ""),
                    capabilities,
                    now,
                    now,
                ),
            )
            return self._dict(conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()) or {}

    def heartbeat(self, agent_id: str, current_job_id: str | None) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE agents SET last_seen = ?, current_job_id = ? WHERE id = ?",
                (iso(), current_job_id, agent_id),
            )
            if current_job_id:
                conn.execute(
                    """UPDATE jobs SET lease_until = ?, updated_at = ?
                       WHERE id = ? AND assigned_agent_id = ? AND status = 'processing'""",
                    (iso(utcnow() + timedelta(minutes=5)), iso(), current_job_id, agent_id),
                )
            return cursor.rowcount == 1

    def claim_job(self, agent_id: str) -> dict[str, Any] | None:
        now = iso()
        lease = iso(utcnow() + timedelta(minutes=5))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """UPDATE jobs SET status = 'queued', assigned_agent_id = NULL,
                       lease_until = NULL, updated_at = ?
                       WHERE status = 'processing' AND lease_until IS NOT NULL AND lease_until < ?""",
                    (now, now),
                )
                row = conn.execute(
                    """SELECT j.id FROM jobs j JOIN profiles p ON p.id = j.profile_id
                       WHERE j.status = 'queued' AND j.scheduled_at <= ? AND p.enabled = 1
                       ORDER BY j.scheduled_at ASC, j.created_at ASC LIMIT 1""",
                    (now,),
                ).fetchone()
                if not row:
                    conn.execute("COMMIT")
                    return None
                job_id = row["id"]
                conn.execute(
                    """UPDATE jobs SET status = 'processing', assigned_agent_id = ?,
                       lease_until = ?, attempt_count = attempt_count + 1, updated_at = ?
                       WHERE id = ?""",
                    (agent_id, lease, now, job_id),
                )
                conn.execute(
                    "UPDATE agents SET current_job_id = ?, last_seen = ? WHERE id = ?",
                    (job_id, now, agent_id),
                )
                conn.execute(
                    "INSERT INTO job_events (job_id, level, message, created_at) VALUES (?, 'info', ?, ?)",
                    (job_id, f"Agent {agent_id[:8]} đã nhận job", now),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get_job(job_id, include_events=False)

    def add_event(self, job_id: str, agent_id: str, level: str, message: str) -> bool:
        level = level if level in {"debug", "info", "warning", "error"} else "info"
        with self.connect() as conn:
            owned = conn.execute(
                "SELECT 1 FROM jobs WHERE id = ? AND assigned_agent_id = ?",
                (job_id, agent_id),
            ).fetchone()
            if not owned:
                return False
            conn.execute(
                "INSERT INTO job_events (job_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                (job_id, level, message[:2000], iso()),
            )
            return True

    def finish_job(
        self,
        job_id: str,
        agent_id: str,
        status: str,
        artifact_path: str | None = None,
        error: str | None = None,
    ) -> bool:
        if status not in {"completed", "failed"}:
            raise ValueError("Trạng thái hoàn tất không hợp lệ")
        now = iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE jobs SET status = ?, artifact_path = ?, error = ?,
                   lease_until = NULL, updated_at = ?
                   WHERE id = ? AND assigned_agent_id = ? AND status = 'processing'""",
                (status, artifact_path, error, now, job_id, agent_id),
            )
            if not cursor.rowcount:
                return False
            conn.execute(
                "UPDATE agents SET current_job_id = NULL, last_seen = ? WHERE id = ?",
                (now, agent_id),
            )
            message = "Job hoàn tất" if status == "completed" else f"Job thất bại: {error or 'Không rõ lỗi'}"
            conn.execute(
                "INSERT INTO job_events (job_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                (job_id, "info" if status == "completed" else "error", message[:2000], now),
            )
            return True
