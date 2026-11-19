"""[외부 API 툴] F3002 후보 조회 — Smogon 월간 사용률 통계 (www.smogon.com/stats).

상대 포켓몬이 '실제로 어떤 기술·도구·특성·노력치로 나오는지'의 확률 분포와,
포맷 전체의 사용률 상위 목록(후보 풀)을 돌려준다.

- 기준 미지정 시 기본값: 포맷 = DEFAULT_FORMAT, 월 = 가장 최근 집계월, 레이팅 = DEFAULT_RATING
- 통계는 과거 한 달 집계만 있으므로 요청한 월이 없으면(미래 등) 가장 최근 집계월로 조회하고
  estimated=True와 사유를 함께 밝힌다.
"""
from __future__ import annotations

import re

from app import config
from app.domain.roles import aggregate as aggregate_roles
from app.tools.common import ErrorCode, ToolError, http_get, ok, safe_tool, to_id
from app.tools.names import resolve_one

INDEX_TTL = 6 * 3600          # 월 목록/포맷 목록은 자주 바뀌지 않음
MONTH_TTL = 30 * 24 * 3600    # 한 달 집계 파일은 공개 후 바뀌지 않음
MONTH_LOOKBACK = 24
_MONTH_RE = re.compile(r'href="(\d{4}-\d{2})/"')


def _months() -> list[str]:
    html = http_get(f"{config.SMOGON_STATS_BASE}/", ttl=INDEX_TTL, as_text=True)
    months = sorted(set(_MONTH_RE.findall(html)))
    if not months:
        raise ToolError(ErrorCode.UPSTREAM_ERROR, "Smogon 통계 월 목록을 읽지 못했습니다.")
    return months


def _chaos_files(month: str) -> dict[str, list[int]]:
    """{포맷: [사용 가능한 레이팅 컷]}"""
    html = http_get(f"{config.SMOGON_STATS_BASE}/{month}/chaos/", ttl=INDEX_TTL, as_text=True)
    files: dict[str, list[int]] = {}
    for fmt, cut in re.findall(r'href="([a-z0-9]+)-(\d+)\.json"', html):
        files.setdefault(fmt, []).append(int(cut))
    return {k: sorted(v) for k, v in files.items()}


def _pct_table(table: dict, total: float, top_n: int, rename=None) -> list[dict]:
    rows = sorted(table.items(), key=lambda kv: -kv[1])[:top_n]
    out = []
    for key, weight in rows:
        if total <= 0:
            break
        out.append({"name": rename(key) if rename else key, "pct": round(weight / total * 100, 1)})
    return out


def _spread(key: str) -> dict:
    nature, _, evs = key.partition(":")
    vals = [int(x) for x in evs.split("/")] if evs else []
    stats = ["hp", "atk", "def", "spa", "spd", "spe"]
    return {"nature": nature, "points": dict(zip(stats, vals))}


def _counters(table: dict, top_n: int) -> list[dict]:
    rows = []
    for name, v in table.items():
        n, p, d = (v["n"], v["p"], v["d"]) if isinstance(v, dict) else (v[0], v[1], v[2])
        rows.append({"name": name, "battles": round(n), "score": round((p - 4 * d) * 100, 1)})
    rows.sort(key=lambda r: -r["score"])
    return rows[:top_n]


def _match_form(sid: str, by_id: dict[str, str]) -> str | None:
    """PokeAPI와 Smogon의 폼 표기 차이 보정.

    'ogerponwellspringmask' -> 'Ogerpon-Wellspring', 'indeedeefemale' -> 'Indeedee-F'처럼
    Smogon 이름이 요청 ID의 앞부분과 일치하면 가장 긴 것을 고른다.
    단, 폼(rotomwash)을 기본 종(rotom)으로 잘못 붙이지 않도록 후보 자체가 폼 표기일 때만 인정한다.
    """
    cands = [k for k in by_id if sid.startswith(k) and len(k) > 4 and k != sid]
    cands = [k for k in cands if any(o != k and k.startswith(o) for o in by_id) or sid[len(k):] in ("mask", "male", "female")]
    return by_id[max(cands, key=len)] if cands else None


@safe_tool
def get_meta_usage(
    pokemon: list[str] | None = None,
    format: str | None = None,
    month: str | None = None,
    rating: int | None = None,
    top_n: int = 6,
) -> dict:
    if top_n < 1 or top_n > 30:
        raise ToolError(ErrorCode.BAD_ARGUMENT, "top_n은 1~30 사이여야 합니다.")
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ToolError(ErrorCode.BAD_ARGUMENT, "month는 YYYY-MM 형식이어야 합니다.")
    pokemon = [p for p in (pokemon or []) if str(p).strip()]
    if len(pokemon) > 12:
        raise ToolError(ErrorCode.BAD_ARGUMENT, "pokemon은 최대 12마리까지 조회합니다.")

    fmt = to_id(format) if format else config.DEFAULT_FORMAT
    notes: list[str] = []
    if not format:
        notes.append(f"포맷이 지정되지 않아 기본값 {fmt}(싱글 6→3 선출)로 조회했습니다.")

    # 1) 집계월 결정
    months = _months()
    used_month = None
    estimated = False
    if month and month in months:
        used_month = month
    else:
        # 요청 월이 없거나(미래/미공개) 지정하지 않음 → 해당 포맷이 있는 가장 최근 월
        for m in reversed(months[-MONTH_LOOKBACK:]):  # 끝난 시즌(본가) 통계도 찾도록 넉넉히
            if fmt in _chaos_files(m):
                used_month = m
                break
        if used_month is None:
            raise ToolError(ErrorCode.NOT_FOUND, f"최근 {MONTH_LOOKBACK}개월 통계에 포맷 '{fmt}'이(가) 없습니다.")
        if month:
            estimated = True
            notes.append(f"{month} 통계는 아직 없어 가장 최근 집계월 {used_month} 기준으로 추정했습니다.")
        else:
            notes.append(f"집계월이 지정되지 않아 가장 최근 {used_month} 통계를 사용했습니다.")

    files = _chaos_files(used_month)
    if fmt not in files:
        similar = [f for f in files if fmt[:6] in f][:8]
        raise ToolError(ErrorCode.NOT_FOUND, f"{used_month}에 포맷 '{fmt}' 통계가 없습니다. 비슷한 포맷: {similar}")

    # 2) 레이팅 컷 결정 (포맷마다 0/1500/1630/1760 또는 0/1500/1695/1825 등으로 다름)
    want = rating if rating is not None else config.DEFAULT_RATING
    cuts = files[fmt]
    used_rating = min(cuts, key=lambda c: abs(c - want))
    if used_rating != want:
        notes.append(f"레이팅 {want} 컷 통계가 없어 가장 가까운 {used_rating} 컷을 사용했습니다.")

    url = f"{config.SMOGON_STATS_BASE}/{used_month}/chaos/{fmt}-{used_rating}.json"
    stats = http_get(url, ttl=MONTH_TTL)
    data = stats.get("data") or {}
    if not data:
        raise ToolError(ErrorCode.NO_DATA, "통계 파일에 포켓몬 데이터가 비어 있습니다.")

    meta = {
        "format": fmt,
        "month": used_month,
        "rating": used_rating,
        "battles": stats.get("info", {}).get("number of battles"),
        "requested": {"format": format, "month": month, "rating": rating},
        "estimated": estimated,
        "notes": notes,
        "source": "Smogon usage stats (chaos)",
    }

    # 3-a) 포켓몬을 지정하지 않음 → 사용률 상위 후보 목록
    if not pokemon:
        ranking = sorted(data.items(), key=lambda kv: -kv[1].get("usage", 0))[: max(top_n, 12)]
        return ok({**meta, "mode": "ranking", "ranking": [
            {"rank": i + 1, "name": name, "showdown_id": to_id(name), "usage_pct": round(v.get("usage", 0) * 100, 2)}
            for i, (name, v) in enumerate(ranking)
        ]})

    # 3-b) 지정한 포켓몬의 예상 세트
    by_id = {to_id(k): k for k in data}
    results = []
    for raw in pokemon:
        resolved = resolve_one(raw, "pokemon") if not raw.isascii() else None
        sid = resolved["showdown_id"] if resolved and resolved.get("found") else to_id(raw)
        key = by_id.get(sid)
        if key is None:
            key = _match_form(sid, by_id)
            if key:
                notes.append(f"'{raw}'은(는) Smogon에서 '{key}'(으)로 표기되어 있어 그 이름으로 조회했습니다.")
        if key is None:
            results.append({"input": raw, "found": False, "reason": "NOT_IN_FORMAT",
                            "message": f"{fmt} {used_month} 통계에 없는 포켓몬입니다 (사용률이 매우 낮거나 이름이 다름)."})
            continue
        v = data[key]
        total = sum(v.get("Abilities", {}).values())  # 포켓몬당 특성은 하나 → 전체 가중치
        entry = {
            "input": raw,
            "found": True,
            "name": key,
            "showdown_id": sid,
            "usage_pct": round(v.get("usage", 0) * 100, 2),
            "moves": _pct_table(v.get("Moves", {}), total, top_n),
            "items": _pct_table(v.get("Items", {}), total, top_n),
            "abilities": _pct_table(v.get("Abilities", {}), total, 3),
            "spreads": [{**_spread(s["name"]), "pct": s["pct"]} for s in _pct_table(v.get("Spreads", {}), total, 6)],
            "teammates": _pct_table(v.get("Teammates", {}), total, 5),
            "checks_counters": _counters(v.get("Checks and Counters", {}), 5),
        }
        # 모든 배분을 운용 형태(물리 고속 딜러 / 특수 딜탱 / 물리막이 …)로 분류한 비율
        entry["roles"] = aggregate_roles(v.get("Spreads", {}), total)
        tera = {k: w for k, w in v.get("Tera Types", {}).items() if k != "nothing"}
        entry["tera_types"] = _pct_table(tera, total, 3) if tera else []
        if total <= 0:
            entry["moves"] = []
            entry["no_data_reason"] = "가중치 합계가 0이라 비율을 계산할 수 없습니다."
        results.append(entry)

    if not any(r["found"] for r in results):
        raise ToolError(ErrorCode.NO_DATA, f"{fmt} {used_month} 통계에서 요청한 포켓몬을 하나도 찾지 못했습니다.")
    return ok({**meta, "mode": "sets", "pokemon": results})
