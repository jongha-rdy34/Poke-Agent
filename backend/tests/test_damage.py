"""실수치·데미지 계산 — 기대값은 공식 계산기 @smogon/calc 0.10.0 (Gen 9, Lv50, 특성·도구 없음)으로 뽑은 값."""
import pytest

from app.domain.damage import calc_move, damage_rolls, ko_summary, move_distribution
from app.domain.stats import calc_stat, full_stats

GARCHOMP = {"hp": 108, "atk": 130, "def": 95, "spa": 80, "spd": 85, "spe": 102}
GHOLDENGO = {"hp": 87, "atk": 60, "def": 95, "spa": 133, "spd": 91, "spe": 84}
MEOWSCARADA = {"hp": 76, "atk": 110, "def": 70, "spa": 81, "spd": 70, "spe": 123}
KINGAMBIT = {"hp": 100, "atk": 135, "def": 120, "spa": 60, "spd": 85, "spe": 50}
CORVIKNIGHT = {"hp": 98, "atk": 87, "def": 105, "spa": 53, "spd": 85, "spe": 67}


def ev(**k):
    return {s: k.get(s, 0) for s in ("hp", "atk", "def", "spa", "spd", "spe")}


def test_ev_stats_match_reference():
    assert full_stats(GARCHOMP, ev(atk=252, spe=252), "Jolly", "ev") == \
        {"hp": 183, "atk": 182, "def": 115, "spa": 90, "spd": 105, "spe": 169}


def test_champions_sp_stats():
    # Showdown champions mod: HP = 종족값+SP+75, 그 외 = (종족값+SP+20)×성격
    assert calc_stat("hp", 108, 2, "Jolly", "sp") == 185
    assert calc_stat("atk", 130, 32, "Jolly", "sp") == 182
    assert calc_stat("spe", 102, 32, "Jolly", "sp") == 169          # 252 노력치 명랑과 같은 값
    assert calc_stat("spa", 80, 0, "Jolly", "sp") == 90


def test_earthquake_rolls_match_smogon_calc():
    a = full_stats(GARCHOMP, ev(atk=252, spe=252), "Jolly", "ev")
    d = full_stats(GHOLDENGO, ev(hp=252, **{"def": 252}), "Bold", "ev")
    rolls = damage_rolls(100, a["atk"], d["def"], True, 2)
    assert sorted(rolls) == [128, 128, 132, 132, 134, 134, 138, 138, 140, 140, 144, 144, 146, 146, 150, 152]


def test_doubles_spread_match_smogon_calc():
    a = full_stats(GARCHOMP, ev(atk=252), "Adamant", "ev")
    d = full_stats(GHOLDENGO, ev(hp=4), "Timid", "ev")
    mv = {"id": "earthquake", "base_power": 100, "category": "Physical", "type": "Ground", "target": "allAdjacent"}
    r = calc_move(mv, {"stats": a, "types": ["Dragon", "Ground"]}, {"stats": d, "types": ["Steel", "Ghost"]}, 2, doubles=True)
    assert (r["min"], r["max"]) == (146, 174) and r["spread"] is True


def test_triple_axel_total_matches_smogon_calc():
    a = full_stats(MEOWSCARADA, ev(atk=252), "Adamant", "ev")
    d = full_stats(GARCHOMP, ev(hp=4), "Jolly", "ev")
    mv = {"id": "tripleaxel", "base_power": 20, "category": "Physical", "type": "Ice", "multihit": 3}
    r = calc_move(mv, {"stats": a, "types": ["Grass", "Dark"]}, {"stats": d, "types": ["Dragon", "Ground"]}, 4)
    assert (r["min"], r["max"]) == (284, 344) and r["hits"] == "3회"
    assert r["ko"] == "확정 1타"  # 체력 184 < 최소 284


def test_body_press_uses_defense_matches_smogon_calc():
    a = full_stats(CORVIKNIGHT, ev(hp=252, **{"def": 252}), "Impish", "ev")
    d = full_stats(KINGAMBIT, ev(hp=252), "Adamant", "ev")
    mv = {"id": "bodypress", "base_power": 80, "category": "Physical", "type": "Fighting", "override_offensive_stat": "def"}
    r = calc_move(mv, {"stats": a, "types": ["Flying", "Steel"]}, {"stats": d, "types": ["Dark", "Steel"]}, 4)
    assert (r["min"], r["max"]) == (152, 180)


def test_ko_probabilities():
    assert ko_summary({100: 0.5, 120: 0.5}, 110)["ko"] == "난수 1타 (50.0%)"
    assert ko_summary({60: 1.0}, 110)["ko"] == "확정 2타"
    s = ko_summary({50: 0.5, 60: 0.5}, 110)  # 두 번 합이 110 이상: (60,60)=25%, (50,60)·(60,50) = 110 → 75%
    assert s["ko"] == "난수 2타 (75.0%)"


def test_variable_multihit_distribution_sums_to_one():
    d = move_distribution([[10] * 16], {2: 0.35, 3: 0.35, 4: 0.15, 5: 0.15})
    assert abs(sum(d.values()) - 1) < 1e-9 and min(d) == 20 and max(d) == 50
