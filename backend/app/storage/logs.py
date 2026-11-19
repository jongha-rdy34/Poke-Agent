"""F3013, F3014 실행 로그 저장소. 추가·조회·삭제만 있고 수정 함수는 두지 않는다."""
from __future__ import annotations

import json

from app.storage.db import connect


def append(event: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO run_events (session_id, run_id, seq, ts, type, step, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event["session_id"], event["run_id"], event["seq"], event["ts"], event["type"],
             event.get("step"), json.dumps(event.get("payload", {}), ensure_ascii=False, default=str)),
        )


def list_sessions() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT session_id,
                   COUNT(DISTINCT run_id)                           AS runs,
                   COUNT(*)                                         AS events,
                   MIN(ts)                                          AS first_ts,
                   MAX(ts)                                          AS last_ts,
                   SUM(CASE WHEN type = 'tool_call_end' THEN 1 ELSE 0 END) AS tool_calls,
                   SUM(CASE WHEN type = 'replan' THEN 1 ELSE 0 END)        AS replans
            FROM run_events GROUP BY session_id ORDER BY MAX(id) DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, session_id, run_id, seq, ts, type, step, payload FROM run_events "
            "WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def delete_session(session_id: str) -> int:
    with connect() as conn:
        cur = conn.execute("DELETE FROM run_events WHERE session_id = ?", (session_id,))
    return cur.rowcount
