"""9세대 데미지 계산 (Pokémon Showdown sim/battle-actions.ts getDamage/modifyDamage와 같은 순서·반올림).

반영: Lv50, 위력, 공격/방어 실수치, 랜덤 16단계(85~100%), 자속 1.5배, 타입 상성, 더블 범위기 0.75배, 연속기.
반영하지 않음(가정으로 밝힘): 급소, 날씨·필드, 도구(구애·생명의구슬 등), 특성(적응력 등), 랭크 변화, 화상, 벽.
"""
from __future__ import annotations

from itertools import product

LEVEL = 50
ROLLS = range(16)          # 100 - r, r = 0..15  → 100% ~ 85%
SPREAD_TARGETS = {"allAdjacent", "allAdjacentFoes"}
VARIABLE_HITS = {2: 0.35, 3: 0.35, 4: 0.15, 5: 0.15}  # 2~5회 연속기의 기본 확률 (특성·도구 없음)


def _modify(value: int, mult: float) -> int:
    """Showdown battle.modify: 4096 기준 고정소수점, 0.5 내림 반올림."""
    modifier = int(mult * 4096)
    return (value * modifier + 2048 - 1) // 4096


def damage_rolls(power: int, attack: int, defense: int, stab: bool, effectiveness: float,
                 spread: bool = False) -> list[int]:
    """한 번 맞혔을 때의 16가지 데미지."""
    if effectiveness == 0 or power <= 0:
        return [0] * 16
    base = (((2 * LEVEL // 5 + 2) * power * attack) // defense) // 50
    base += 2
    if spread:
        base = _modify(base, 0.75)
    out = []
    for r in ROLLS:
        d = base * (100 - r) // 100
        if stab:
            d = _modify(d, 1.5)
        e = effectiveness
        while e >= 2:
            d *= 2
            e /= 2
        while 0 < e <= 0.5:
            d //= 2
            e *= 2
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


def calc_move(move: dict, attacker: dict, defender: dict, effectiveness: float, doubles: bool = False) -> dict | None:
    """move: Showdown 기술 정보, attacker/defender: {stats, types}. 위력이 가변(0)이면 None."""
    power = move.get("base_power") or 0
    if power <= 0 or move.get("category") == "Status":
        return None
    physical = move["category"] == "Physical"
    atk_stat = move.get("override_offensive_stat") or ("atk" if physical else "spa")
    def_stat = move.get("override_defensive_stat") or ("def" if physical else "spd")
    src = defender if move.get("override_offensive_pokemon") == "target" else attacker
    attack, defense = src["stats"][atk_stat], defender["stats"][def_stat]
    stab = move["type"] in attacker["types"]
    spread = doubles and move.get("target") in SPREAD_TARGETS

    hits = move.get("multihit")
    if move.get("id") in ("tripleaxel", "triplekick"):
        step = power
        per_hit = [damage_rolls(step * (i + 1), attack, defense, stab, effectiveness, spread) for i in range(3)]
        dist, hits_label = move_distribution(per_hit), "3회"
    elif isinstance(hits, list):
        rolls = damage_rolls(power, attack, defense, stab, effectiveness, spread)
        dist, hits_label = move_distribution([rolls], VARIABLE_HITS), f"{hits[0]}~{hits[1]}회"
    elif isinstance(hits, int) and hits > 1:
        rolls = damage_rolls(power, attack, defense, stab, effectiveness, spread)
        dist, hits_label = move_distribution([rolls] * hits), f"{hits}회"
    else:
        dist, hits_label = _dist(damage_rolls(power, attack, defense, stab, effectiveness, spread)), None
    out = ko_summary(dist, defender["stats"]["hp"])
    out.update({"hits": hits_label, "stab": stab, "spread": spread,
                "attack_stat": atk_stat, "defense_stat": def_stat})
    return out
