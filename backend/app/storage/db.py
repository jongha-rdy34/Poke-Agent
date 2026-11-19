"""SQLite 연결과 테이블 생성. 계획(plans)과 실행 로그(run_events) 두 저장소를 둔다."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS run_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    run_id     TEXT    NOT NULL,
    seq        INTEGER NOT NULL,
    ts         TEXT    NOT NULL,
    type       TEXT    NOT NULL,
    step       TEXT,
    payload    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_events_session ON run_events(session_id, id);

CREATE TABLE IF NOT EXISTS plans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    battle_date   TEXT NOT NULL,
    start_time    TEXT NOT NULL,
    end_time      TEXT NOT NULL,
    format        TEXT NOT NULL,
    my_team       TEXT NOT NULL,
    opponent_team TEXT NOT NULL,
    selected      TEXT NOT NULL,
    lead          TEXT,
    move_plan     TEXT NOT NULL,
    basis         TEXT,
    memo          TEXT,
    session_id    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_date ON plans(battle_date);
"""


def db_path() -> Path:
    return Path(config.DB_PATH)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
