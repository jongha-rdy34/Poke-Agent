"""Lv50 실수치 계산. 개체값은 31로 고정한다 (Champions는 항상 31, 본가 랭크도 사실상 31).

- 노력치(EV, 최대 252): 본가 공식
- SP(스탯당 최대 32, 총 66): 포켓몬 챔피언스. Pokémon Showdown `data/mods/champions/scripts.ts`의 statModify와 같다.
    HP = 종족값 + SP + 75,  그 외 = (종족값 + SP + 20) × 성격 보정
"""
from __future__ import annotations

from app.domain.roles import NATURES

LEVEL = 50


def _nature_mult(stat: str, nature: str | None) -> int:
    plus, minus, _ = NATURES.get(nature or "", (None, None, None))
    return 110 if stat == plus else 90 if stat == minus else 100


def calc_stat(stat: str, base: int, points: int, nature: str | None, system: str) -> int:
    """system: 'sp'(챔피언스) 또는 'ev'(본가)."""
    if system == "sp":
        if stat == "hp":
            return base + points + 75
        raw = base + points + 20
    else:
        core = (2 * base + 31 + points // 4) * LEVEL // 100
        if stat == "hp":
            return core + LEVEL + 10
        raw = core + 5
    return raw * _nature_mult(stat, nature) // 100


def max_points(system: str) -> int:
    return 32 if system == "sp" else 252


def system_for(fmt: str | None) -> str:
    return "sp" if fmt and "champions" in fmt else "ev"


def full_stats(base: dict, points: dict, nature: str | None, system: str) -> dict:
    return {s: calc_stat(s, base.get(s) or 0, points.get(s, 0), nature, system)
            for s in ("hp", "atk", "def", "spa", "spd", "spe")}
