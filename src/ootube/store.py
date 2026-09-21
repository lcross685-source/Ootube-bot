"""Durable state.

A low-maintenance bot needs memory. Without it, every run re-discovers the
same trends, re-publishes the same video, and has no idea how much API quota
it already burned today. SQLite is used because it needs no server and the
file can be committed as a CI artifact or kept on a volume.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Topic, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    fingerprint   TEXT PRIMARY KEY,
    term          TEXT NOT NULL,
    niche         TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    score         REAL DEFAULT 0,
    status        TEXT DEFAULT 'seen',
    reason        TEXT DEFAULT '',
    payload       TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS published (
    video_id      TEXT PRIMARY KEY,
    fingerprint   TEXT,
    topic_key     TEXT,
    title         TEXT,
    url           TEXT,
    niche         TEXT,
    uploaded_at   TEXT NOT NULL,
    scheduled_for TEXT,
    dry_run       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quota (
    day           TEXT PRIMARY KEY,
    units         INTEGER DEFAULT 0,
    upload_calls  INTEGER DEFAULT 0,
    search_calls  INTEGER DEFAULT 0
);

-- Tracks the newest known generation of a product family, e.g.
-- ('samsung galaxy s', 25, '2026-01-20'). The freshness gate uses this to
-- reject a topic about the S23 once an S25 has been observed.
CREATE TABLE IF NOT EXISTS generations (
    family        TEXT PRIMARY KEY,
    latest        REAL NOT NULL,
    label         TEXT,
    observed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stage         TEXT,
    status        TEXT,
    started_at    TEXT,
    finished_at   TEXT,
    detail        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS approvals (
    fingerprint   TEXT PRIMARY KEY,
    topic_key     TEXT,
    title         TEXT,
    created_at    TEXT,
    decision      TEXT DEFAULT 'pending',
    payload       TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_published_uploaded ON published(uploaded_at);
CREATE INDEX IF NOT EXISTS idx_published_sched   ON published(scheduled_for);
CREATE INDEX IF NOT EXISTS idx_topics_status     ON topics(status);
"""


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Store:
    """Thin SQLite wrapper. All timestamps are stored as UTC ISO-8601."""

    def __init__(self, path: str | Path = "data/ootube.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ---------------------------------------------------------------- topics
    def record_topic(self, topic: Topic, status: str = "seen", reason: str = "") -> None:
        now = _iso(utcnow())
        payload = json.dumps(
            {
                "sources": topic.sources,
                "demand": topic.demand,
                "saturation": topic.saturation,
                "projected_views": topic.projected_views,
                "expected_revenue_usd": topic.expected_revenue_usd,
                "angle": topic.angle,
            }
        )
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO topics (fingerprint, term, niche, first_seen, last_seen,
                                    score, status, reason, payload)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    score     = excluded.score,
                    status    = excluded.status,
                    reason    = excluded.reason,
                    payload   = excluded.payload
                """,
                (
                    topic.fingerprint,
                    topic.term,
                    topic.niche,
                    now,
                    now,
                    topic.score,
                    status,
                    reason,
                    payload,
                ),
            )

    def topic_status(self, fingerprint: str) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM topics WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return row["status"] if row else None

    def first_seen(self, fingerprint: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT first_seen FROM topics WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return _parse(row["first_seen"]) if row else None

    # ------------------------------------------------------------- published
    def record_published(
        self,
        *,
        video_id: str,
        topic: Topic,
        title: str,
        url: str,
        scheduled_for: datetime,
        dry_run: bool = False,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO published
                    (video_id, fingerprint, topic_key, title, url, niche,
                     uploaded_at, scheduled_for, dry_run)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    video_id,
                    topic.fingerprint,
                    topic.key,
                    title,
                    url,
                    topic.niche,
                    _iso(utcnow()),
                    _iso(scheduled_for),
                    int(dry_run),
                ),
            )
            # Upsert rather than update: a topic published without having
            # been recorded first would otherwise leave no row at all, so its
            # status would silently read back as unknown.
            now = _iso(utcnow())
            conn.execute(
                """
                INSERT INTO topics (fingerprint, term, niche, first_seen, last_seen,
                                    score, status, reason, payload)
                VALUES (?,?,?,?,?,?, 'published', '', '{}')
                ON CONFLICT(fingerprint) DO UPDATE SET
                    status    = 'published',
                    last_seen = excluded.last_seen
                """,
                (
                    topic.fingerprint, topic.term, topic.niche,
                    now, now, topic.score,
                ),
            )

    def is_published(self, fingerprint: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM published WHERE fingerprint = ? LIMIT 1", (fingerprint,)
        ).fetchone()
        return row is not None

    def published_since(self, since: datetime) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM published WHERE uploaded_at >= ? ORDER BY uploaded_at",
                (_iso(since),),
            )
        )

    def scheduled_after(self, when: datetime) -> list[datetime]:
        """Publish slots already taken, used to avoid double-booking a time."""
        rows = self._conn.execute(
            "SELECT scheduled_for FROM published WHERE scheduled_for >= ? ORDER BY scheduled_for",
            (_iso(when),),
        ).fetchall()
        return [d for d in (_parse(r["scheduled_for"]) for r in rows) if d]

    def recent_terms(self, days: int = 30) -> list[str]:
        cutoff = _iso(utcnow() - timedelta(days=days))
        rows = self._conn.execute(
            "SELECT topic_key FROM published WHERE uploaded_at >= ?", (cutoff,)
        ).fetchall()
        return [r["topic_key"] for r in rows]

    # ----------------------------------------------------------------- quota
    def quota_today(self, day: str | None = None) -> dict[str, int]:
        day = day or utcnow().strftime("%Y-%m-%d")
        row = self._conn.execute("SELECT * FROM quota WHERE day = ?", (day,)).fetchone()
        if not row:
            return {"units": 0, "upload_calls": 0, "search_calls": 0}
        return {
            "units": row["units"],
            "upload_calls": row["upload_calls"],
            "search_calls": row["search_calls"],
        }

    def add_quota(
        self, units: int = 0, upload_calls: int = 0, search_calls: int = 0, day: str | None = None
    ) -> None:
        day = day or utcnow().strftime("%Y-%m-%d")
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO quota (day, units, upload_calls, search_calls) VALUES (?,?,?,?)
                ON CONFLICT(day) DO UPDATE SET
                    units        = units + excluded.units,
                    upload_calls = upload_calls + excluded.upload_calls,
                    search_calls = search_calls + excluded.search_calls
                """,
                (day, units, upload_calls, search_calls),
            )

    # ----------------------------------------------------------- generations
    def observe_generation(self, family: str, number: float, label: str = "") -> None:
        """Remember the newest generation seen for a product family.

        Only moves forward: seeing an old article about the S23 must not
        un-learn that the S25 exists.
        """
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO generations (family, latest, label, observed_at) VALUES (?,?,?,?)
                ON CONFLICT(family) DO UPDATE SET
                    latest      = MAX(generations.latest, excluded.latest),
                    label       = CASE WHEN excluded.latest >= generations.latest
                                       THEN excluded.label ELSE generations.label END,
                    observed_at = CASE WHEN excluded.latest >= generations.latest
                                       THEN excluded.observed_at ELSE generations.observed_at END
                """,
                (family, float(number), label, _iso(utcnow())),
            )

    def latest_generation(self, family: str) -> float | None:
        row = self._conn.execute(
            "SELECT latest FROM generations WHERE family = ?", (family,)
        ).fetchone()
        return float(row["latest"]) if row else None

    def all_generations(self) -> dict[str, float]:
        rows = self._conn.execute("SELECT family, latest FROM generations").fetchall()
        return {r["family"]: float(r["latest"]) for r in rows}

    # -------------------------------------------------------------- approval
    def queue_approval(self, topic: Topic, title: str, payload: dict[str, Any]) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO approvals
                    (fingerprint, topic_key, title, created_at, decision, payload)
                VALUES (?,?,?,?,'pending',?)
                """,
                (topic.fingerprint, topic.key, title, _iso(utcnow()), json.dumps(payload)),
            )

    def pending_approvals(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM approvals WHERE decision='pending' ORDER BY created_at"
            )
        )

    def decide_approval(self, fingerprint: str, decision: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE approvals SET decision = ? WHERE fingerprint = ?",
                (decision, fingerprint),
            )

    # ------------------------------------------------------------------ runs
    def log_run(self, stage: str, status: str, detail: str = "") -> None:
        now = _iso(utcnow())
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO runs (stage, status, started_at, finished_at, detail) VALUES (?,?,?,?,?)",
                (stage, status, now, now, detail[:2000]),
            )

    def recent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(
            self._conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        )
