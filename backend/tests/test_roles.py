"""운용 형태 분류(딜러/탱커)와 상대 파티 물리/특수 비율."""
import pytest

from app.domain.roles import aggregate, classify, team_profile


@pytest.mark.parametrize("nature,points,role,code", [
    ("Jolly", {"hp": 2, "atk": 32, "spe": 32}, "물리 고속 딜러", "AS"),        # AS 한카리아스
    ("Timid", {"hp": 2, "spa": 32, "spe": 32}, "특수 고속 딜러", "CS"),        # CS 메가한카리아스Z
    ("Modest", {"hp": 32, "spa": 32, "spd": 2}, "특수 딜탱", "HC"),            # HC 누리레느
    ("Adamant", {"hp": 32, "atk": 32}, "물리 딜탱", "HA"),
    ("Impish", {"hp": 32, "def": 32, "spd": 2}, "물리막이", "HB"),             # 내구 보정 스텔스록 깔이
    ("Careful", {"hp": 32, "spd": 32, "def": 2}, "특수막이", "HD"),
    ("Impish", {"hp": 32, "def": 16, "spd": 16}, "양막이", "HBD"),
    ("Jolly", {"hp": 32, "spe": 32}, "고속 서포터", "HS"),
    ("Naive", {"atk": 32, "spa": 32, "spe": 2}, "혼합 딜탱", "AC"),
])
def test_classify_sp(nature, points, role, code):
    c = classify(nature, points, 32)
    assert c["role"] == role and c["code"] == code


def test_classify_ev_system():
    assert classify("Adamant", {"atk": 252, "spe": 252, "hp": 4}, 252)["role"] == "물리 고속 딜러"


def test_aggregate_garchomp_like_distribution():
    spreads = {"Jolly:2/32/0/0/0/32": 60, "Impish:32/0/32/0/2/0": 25, "Timid:2/0/0/32/0/32": 15}
    r = aggregate(spreads, 100)
    roles = {x["role"]: x["pct"] for x in r["roles"]}
    assert roles == {"물리 고속 딜러": 60.0, "물리막이": 25.0, "특수 고속 딜러": 15.0}
    assert r["attacker_pct"] == 75.0 and r["tank_pct"] == 25.0
    assert r["physical_share"] == 80.0  # 딜러 75 중 물리 60
    assert r["point_system"] == "SP(최대 32)"


def test_team_profile_weights_by_attacker_and_skips_unknown():
    phys = {"roles": [], "main_role": "물리 고속 딜러", "attacker_pct": 100, "physical_share": 100}
    wall = {"roles": [], "main_role": "물리막이", "attacker_pct": 0, "physical_share": None}
    spec = {"roles": [], "main_role": "특수 딜탱", "attacker_pct": 100, "physical_share": 0}
    p = team_profile([{"name": "A", "sid": "a", "roles": phys}, {"name": "B", "sid": "b", "roles": spec},
                      {"name": "W", "sid": "w", "roles": wall}, {"name": "X", "sid": "x", "roles": None}],
                     {"a": 100, "b": 0, "w": None, "x": None})
    assert p["physical_pct"] == 50.0 and p["lean"] == "물리·특수 균형"
    assert p["unknown"] == ["X"]  # 통계가 없으면 값을 지어내지 않음
    assert p["walls"] == ["W"] and p["members"][2]["basis"].startswith("공격 배분 없음")
