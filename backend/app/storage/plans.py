"""F3015, F3016 선출 계획 저장소. 같은 날짜에 시간이 겹치는 계획이 있어도 저장은 막지 않고 알려준다."""
from __future__ import annotations

import json
from datetime import datetime

from app.storage.db import connect

JSON_FIELDS = ("my_team", "opponent_team", "selected", "move_plan", "basis")
FIELDS = ("title", "battle_date", "start_time", "end_time", "format", "my_team", "opponent_team",
          "selected", "lead", "move_plan", "basis", "memo", "session_id")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row(r) -> dict:
    d = dict(r)
    for f in JSON_FIELDS:
        d[f] = json.loads(d[f]) if d.get(f) else ([] if f != "basis" else None)
    return d


def _encode(data: dict) -> dict:
    return {k: (json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS and v is not None else v)
            for k, v in data.items() if k in FIELDS}


def find_overlaps(battle_date: str, start_time: str, end_time: str, exclude_id: int | None = None) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, battle_date, start_time, end_time FROM plans "
            "WHERE battle_date = ? AND start_time < ? AND end_time > ? AND id != ? ORDER BY start_time",
            (battle_date, end_time, start_time, exclude_id or -1),
        ).fetchall()
    return [dict(r) for r in rows]


def create(data: dict) -> tuple[dict, list[dict]]:
    overlaps = find_overlaps(data["battle_date"], data["start_time"], data["end_time"])
    row = _encode(data)
    now = _now()
    row["created_at"] = row["updated_at"] = now
    cols = ", ".join(row)
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO plans ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        plan_id = cur.lastrowid
    return get(plan_id), overlaps


def get(plan_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    return _row(r) if r else None


def list_all(battle_date: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM plans", ()
    if battle_date:
        sql, args = sql + " WHERE battle_date = ?", (battle_date,)
    with connect() as conn:
        rows = conn.execute(sql + " ORDER BY battle_date DESC, start_time", args).fetchall()
    return [_row(r) for r in rows]


def update(plan_id: int, changes: dict) -> tuple[dict | None, list[dict]]:
    current = get(plan_id)
    if current is None:
        return None, []
    merged = {**current, **{k: v for k, v in changes.items() if v is not None}}
    row = _encode({k: v for k, v in changes.items() if v is not None})
    if row:
        row["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in row)
        with connect() as conn:
            conn.execute(f"UPDATE plans SET {sets} WHERE id = ?", (*row.values(), plan_id))
    overlaps = find_overlaps(merged["battle_date"], merged["start_time"], merged["end_time"], exclude_id=plan_id)
    return get(plan_id), overlaps


def delete(plan_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    return cur.rowcount > 0
