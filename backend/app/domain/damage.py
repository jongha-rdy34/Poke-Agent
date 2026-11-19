"""9세대 데미지 계산 (Pokémon Showdown sim/battle-actions.ts getDamage/modifyDamage와 같은 순서·반올림).

반영: Lv50, 위력, 공격/방어 실수치, 랜덤 16단계(85~100%), 자속 1.5배, 타입 상성, 더블 범위기 0.75배, 연속기,
      데미지를 바꾸는 특성(app/domain/abilities.py — 테크니션·단단한발톱·적응력·천하장사·-스킨·멀티스케일·털가죽·필터 등).
      타입 무효·반감 특성(부유·저수·두꺼운지방 등)은 타입 상성표 단계에서 반영된다.
반영하지 않음(가정으로 밝힘): 급소, 날씨·필드, 도구(구애·생명의구슬 등), 랭크 변화, 화상, 벽.
"""
from __future__ import annotations

from itertools import product

from app.domain import abilities as AB

LEVEL = 50
ROLLS = range(16)          # 100 - r, r = 0..15  → 100% ~ 85%
SPREAD_TARGETS = {"allAdjacent", "allAdjacentFoes"}
VARIABLE_HITS = {2: 0.35, 3: 0.35, 4: 0.15, 5: 0.15}  # 2~5회 연속기의 기본 확률 (특성·도구 없음)


def _modify(value: int, mult: float) -> int:
    """Showdown battle.modify: 4096 기준 고정소수점, 0.5 내림 반올림."""
    modifier = int(mult * 4096)
    return (value * modifier + 2048 - 1) // 4096


def _chain(mods: list[float]) -> int:
    """Showdown chainModify: 여러 배율을 4096 고정소수점으로 곱한다."""
    m = 4096
    for x in mods:
        m = (m * int(round(x * 4096)) + 2048) >> 12
    return m


def _apply(value: int, chain: int) -> int:
    return (value * chain + 2048 - 1) // 4096


def damage_rolls(power: int, attack: int, defense: int, stab: bool, effectiveness: float,
                 spread: bool = False, stab_mult: float = 1.5, final_mods: list[float] | None = None) -> list[int]:
    """한 번 맞혔을 때의 16가지 데미지. power/attack/defense는 특성 보정을 마친 값."""
    if effectiveness == 0 or power <= 0:
        return [0] * 16
    final = _chain(final_mods) if final_mods else 4096
    base = (((2 * LEVEL // 5 + 2) * power * attack) // defense) // 50
    base += 2
    if spread:
        base = _modify(base, 0.75)
    out = []
    for r in ROLLS:
        d = base * (100 - r) // 100
        if stab:
            d = _modify(d, stab_mult)
        e = effectiveness
        while e >= 2:
            d *= 2
            e /= 2
        while 0 < e <= 0.5:
            d //= 2
            e *= 2
        if final != 4096:
            d = _apply(d, final)
        out.append(max(d, 1))
    return out


def _convolve(a: dict[int, float], b: dict[int, float]) -> dict[int, float]:
    out: dict[int, float] = {}
    for (x, px), (y, py) in product(a.items(), b.items()):
        out[x + y] = out.get(x + y, 0) + px * py
    return out


def _dist(rolls: list[int]) -> dict[int, float]:
    d: dict[int, float] = {}
    for v in rolls:
        d[v] = d.get(v, 0) + 1 / len(rolls)
    return d


def move_distribution(per_hit_rolls: list[list[int]], hit_weights: dict[int, float] | None = None) -> dict[int, float]:
    """연속기: 타마다 독립적으로 난수가 나온다. per_hit_rolls[i] = i번째 타의 16가지 데미지."""
    if hit_weights is None:
        total = _dist(per_hit_rolls[0])
        for rolls in per_hit_rolls[1:]:
            total = _convolve(total, _dist(rolls))
        return total
    out: dict[int, float] = {}
    acc = {0: 1.0}
    for n in range(1, max(hit_weights) + 1):
        acc = _convolve(acc, _dist(per_hit_rolls[min(n, len(per_hit_rolls)) - 1]))
        w = hit_weights.get(n, 0)
        if w <= 0:
            continue
        for k, p in acc.items():
            out[k] = out.get(k, 0) + p * w
    return out


def ko_summary(dist: dict[int, float], hp: int) -> dict:
    """체력 대비 데미지 범위(%)와 확정/난수 n타."""
    lo, hi = min(dist), max(dist)
    p1 = sum(p for d, p in dist.items() if d >= hp)
    two = _convolve(dist, dist)
    p2 = sum(p for d, p in two.items() if d >= hp)
    three = _convolve(two, dist)
    p3 = sum(p for d, p in three.items() if d >= hp)
    if p1 >= 0.9999:
        label = "확정 1타"
    elif p1 > 0:
        label = f"난수 1타 ({p1 * 100:.1f}%)"
    elif p2 >= 0.9999:
        label = "확정 2타"
    elif p2 > 0:
        label = f"난수 2타 ({p2 * 100:.1f}%)"
    elif p3 >= 0.9999:
        label = "확정 3타"
    elif p3 > 0:
        label = f"난수 3타 ({p3 * 100:.1f}%)"
    else:
        label = "4타 이상"
    return {
        "min": lo, "max": hi, "hp": hp,
        "min_pct": round(lo / hp * 100, 1), "max_pct": round(hi / hp * 100, 1),
        "ohko_pct": round(p1 * 100, 1), "two_hko_pct": round(p2 * 100, 1),
        "ko": label,
    }


def calc_move(move: dict, attacker: dict, defender: dict, effectiveness: float, doubles: bool = False,
              type_chart: dict | None = None) -> dict | None:
    """move: Showdown 기술 정보, attacker/defender: {stats, types, ability?}. 위력이 가변(0)이면 None.
    type_chart: 방어측 상성표(특성 무효 포함). -스킨으로 타입이 바뀌면 이 표로 상성을 다시 본다."""
    power = move.get("base_power") or 0
    if power <= 0 or move.get("category") == "Status":
        return None
    physical = move["category"] == "Physical"
    atk_ab, def_ab = attacker.get("ability"), defender.get("ability")
    move_type = AB.effective_type(atk_ab, move)
    if move_type != move["type"] and type_chart:
        effectiveness = type_chart.get(move_type, 1)
    atk_stat = move.get("override_offensive_stat") or ("atk" if physical else "spa")
    def_stat = move.get("override_defensive_stat") or ("def" if physical else "spd")
    src = defender if move.get("override_offensive_pokemon") == "target" else attacker
    attack, defense = src["stats"][atk_stat], defender["stats"][def_stat]
    bp_mods, atk_mods, stab_mult, notes = AB.attacker_mods(atk_ab, move, move_type, physical)
    df_mods, final_mods, dnotes = AB.defender_mods(def_ab, move, move_type, def_stat == "def", effectiveness)
    notes += dnotes
    if bp_mods:
        power = max(1, _apply(power, _chain(bp_mods)))
    if atk_mods and src is attacker:
        attack = _apply(attack, _chain(atk_mods))
    if df_mods:
        defense = _apply(defense, _chain(df_mods))
    stab = move_type in attacker["types"]
    spread = doubles and move.get("target") in SPREAD_TARGETS
    kw = {"stab_mult": stab_mult, "final_mods": final_mods}

    hits = move.get("multihit")
    if move.get("id") in ("tripleaxel", "triplekick"):
        step = power
        per_hit = [damage_rolls(step * (i + 1), attack, defense, stab, effectiveness, spread, **kw) for i in range(3)]
        dist, hits_label = move_distribution(per_hit), "3회"
    elif isinstance(hits, list):
        rolls = damage_rolls(power, attack, defense, stab, effectiveness, spread, **kw)
        dist, hits_label = move_distribution([rolls], VARIABLE_HITS), f"{hits[0]}~{hits[1]}회"
    elif isinstance(hits, int) and hits > 1:
        rolls = damage_rolls(power, attack, defense, stab, effectiveness, spread, **kw)
        dist, hits_label = move_distribution([rolls] * hits), f"{hits}회"
    else:
        dist, hits_label = _dist(damage_rolls(power, attack, defense, stab, effectiveness, spread, **kw)), None
    out = ko_summary(dist, defender["stats"]["hp"])
    out.update({"hits": hits_label, "stab": stab, "spread": spread, "move_type": move_type,
                "attack_stat": atk_stat, "defense_stat": def_stat, "ability_notes": notes})
    return out
