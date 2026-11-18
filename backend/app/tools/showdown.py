"""[외부 API 툴] F3004 보조 정보 검색 — Pokémon Showdown 공개 데이터 (play.pokemonshowdown.com/data).

- get_move_info: 기술의 타입·분류·위력·명중·우선도 (Smogon 통계의 기술 ID를 그대로 받는다)
- get_type_matchup: 공격 타입 → 방어 타입(최대 2개) 상성 배율. 일부 특성 면역/반감 반영
"""
from __future__ import annotations

import json
import re

from app import config
from app.tools.common import ErrorCode, ToolError, http_get, ok, safe_tool, to_id
from app.tools.names import resolve_one

TTL = 24 * 3600

TYPES_KO = {
    "노말": "Normal", "불꽃": "Fire", "물": "Water", "전기": "Electric", "풀": "Grass", "얼음": "Ice",
    "격투": "Fighting", "독": "Poison", "땅": "Ground", "비행": "Flying", "에스퍼": "Psychic", "벌레": "Bug",
    "바위": "Rock", "고스트": "Ghost", "드래곤": "Dragon", "악": "Dark", "강철": "Steel", "페어리": "Fairy",
}
ALL_TYPES = list(TYPES_KO.values())

# 특성으로 인한 상성 변화 (판단에 자주 쓰이는 것만)
ABILITY_MODS: dict[str, dict[str, float]] = {
    "levitate": {"Ground": 0},
    "flashfire": {"Fire": 0}, "wellbakedbody": {"Fire": 0},
    "waterabsorb": {"Water": 0}, "stormdrain": {"Water": 0}, "dryskin": {"Water": 0},
    "voltabsorb": {"Electric": 0}, "lightningrod": {"Electric": 0}, "motordrive": {"Electric": 0},
    "sapsipper": {"Grass": 0}, "eartheater": {"Ground": 0},
    "thickfat": {"Fire": 0.5, "Ice": 0.5}, "heatproof": {"Fire": 0.5},
    "purifyingsalt": {"Ghost": 0.5}, "waterbubble": {"Fire": 0.5},
}
_CODE_TO_MULT = {0: 1.0, 1: 2.0, 2: 0.5, 3: 0.0}


def _norm_type(t: str) -> str | None:
    t = str(t).strip()
    if t in TYPES_KO:
        return TYPES_KO[t]
    cap = t.capitalize()
    return cap if cap in ALL_TYPES else None


def _typechart() -> dict:
    """typechart.js(JS 객체)를 JSON으로 바꿔 읽는다. typechart.json은 제공되지 않는다(404)."""
    js = http_get(f"{config.SHOWDOWN_DATA_BASE}/typechart.js", ttl=TTL, as_text=True)
    body = js[js.index("{"): js.rindex("}") + 1]
    body = re.sub(r"([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', body)
    try:
        return json.loads(body)
    except ValueError:
        raise ToolError(ErrorCode.UPSTREAM_ERROR, "Showdown 타입 상성표 형식이 바뀌어 읽지 못했습니다.")


def _moves() -> dict:
    return http_get(f"{config.SHOWDOWN_DATA_BASE}/moves.json", ttl=TTL)


@safe_tool
def get_move_info(moves: list[str]) -> dict:
    if not moves or any(not str(m).strip() for m in moves):
        raise ToolError(ErrorCode.BAD_ARGUMENT, "moves에 빈 이름이 있습니다.")
    if len(moves) > 24:
        raise ToolError(ErrorCode.BAD_ARGUMENT, "moves는 최대 24개까지 조회합니다.")

    table = _moves()
    results = []
    for raw in moves:
        mid = to_id(raw)
        ko = None
        if not raw.isascii():
            r = resolve_one(raw, "move")
            mid = r["showdown_id"] if r["found"] else ""
            ko = r.get("ko")
        m = table.get(mid)
        if not m:
            results.append({"input": raw, "found": False, "reason": "NOT_FOUND"})
            continue
        if ko is None:
            r = resolve_one(m["name"], "move")
            ko = r.get("ko") if r["found"] else None
        acc = m.get("accuracy")
        results.append({
            "input": raw,
            "found": True,
            "id": mid,
            "name": m.get("name"),
            "ko_name": ko,
            "type": m.get("type"),
            "category": m.get("category"),           # Physical / Special / Status
            "base_power": m.get("basePower"),
            "accuracy": None if acc is True else acc,  # None = 반드시 명중
            "always_hits": acc is True,
            "priority": m.get("priority", 0),
            "contact": bool(m.get("flags", {}).get("contact")),
        })

    if not any(r["found"] for r in results):
        raise ToolError(ErrorCode.NOT_FOUND, f"기술을 찾지 못했습니다: {', '.join(moves)}")
    return ok({"moves": results, "source": "Pokémon Showdown data (moves.json)"})


@safe_tool
def get_type_matchup(
    defend_types: list[str],
    attack_types: list[str] | None = None,
    defender_ability: str | None = None,
) -> dict:
    if not defend_types or len(defend_types) > 2:
        raise ToolError(ErrorCode.BAD_ARGUMENT, "defend_types는 1~2개여야 합니다.")
    dts = [_norm_type(t) for t in defend_types]
    if None in dts:
        bad = [t for t, n in zip(defend_types, dts) if n is None]
        raise ToolError(ErrorCode.NOT_FOUND, f"알 수 없는 타입입니다: {bad}")

    ats_in = attack_types or ALL_TYPES  # 공격 타입 미지정 → 18타입 전체 약점표
    ats = [_norm_type(t) for t in ats_in]
    if None in ats:
        bad = [t for t, n in zip(ats_in, ats) if n is None]
        raise ToolError(ErrorCode.NOT_FOUND, f"알 수 없는 타입입니다: {bad}")

    chart = _typechart()
    ability_mod = ABILITY_MODS.get(to_id(defender_ability)) if defender_ability else None
    notes = []
    if defender_ability and ability_mod is None:
        notes.append(f"특성 '{defender_ability}'은(는) 상성 보정 목록에 없어 반영하지 않았습니다.")

    rows = []
    for at in ats:
        mult = 1.0
        for dt in dts:
            code = chart.get(dt.lower(), {}).get("damageTaken", {}).get(at)
            if code is None:
                raise ToolError(ErrorCode.NO_DATA, f"상성표에 {at}→{dt} 값이 없습니다.")
            mult *= _CODE_TO_MULT[code]
        if ability_mod and at in ability_mod:
            mult *= ability_mod[at]
            notes.append(f"특성 {defender_ability}: {at} 배율 ×{ability_mod[at]}")
        rows.append({"attack_type": at, "multiplier": mult})

    return ok({
        "defend_types": dts,
        "defender_ability": defender_ability,
        "matchups": rows,
        "weak_to": [r["attack_type"] for r in rows if r["multiplier"] > 1],
        "resists": [r["attack_type"] for r in rows if 0 < r["multiplier"] < 1],
        "immune_to": [r["attack_type"] for r in rows if r["multiplier"] == 0],
        "notes": notes,
        "source": "Pokémon Showdown data (typechart.js)",
    })
