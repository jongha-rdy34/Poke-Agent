"""Tool 단위 테스트 — 네트워크 없이 http_get을 가짜 응답으로 바꿔 실행한다.

실행: python -m pytest -q   (backend 폴더에서)
"""
from __future__ import annotations

import pytest

from app.tools import call_tool, list_specs
from app.tools import pokeapi, showdown, smogon
from app.tools.common import ErrorCode, ToolError

INDEX_HTML = '<a href="2026-09/">2026-09/</a><a href="2026-10/">2026-10/</a>'
CHAOS_HTML = '<a href="gen9championsbssregmc-0.json">x</a><a href="gen9championsbssregmc-1500.json">x</a>'
STATS = {
    "info": {"number of battles": 10},
    "data": {
        "Garchomp": {
            "usage": 0.27,
            "Abilities": {"roughskin": 100.0},
            "Moves": {"earthquake": 99.0, "stealthrock": 60.0},
            "Items": {"choicescarf": 30.0},
            "Spreads": {"Jolly:2/32/0/0/0/32": 50.0},
            "Teammates": {"Primarina": 40.0},
            "Checks and Counters": {"Primarina": {"n": 100, "p": 0.6, "d": 0.01}},
            "Tera Types": {"nothing": 100.0},
        }
    },
}


def fake_smogon(url: str, **_):
    if url.endswith("/stats/"):
        return INDEX_HTML
    if url.endswith("/chaos/"):
        return CHAOS_HTML
    return STATS


@pytest.fixture
def smogon_offline(monkeypatch):
    monkeypatch.setattr(smogon, "http_get", fake_smogon)


# --- F3001 -----------------------------------------------------------------
def test_specs_have_name_description_schema():
    specs = list_specs()
    assert {s["name"] for s in specs} >= {"get_meta_usage", "get_pokemon_detail", "get_move_info", "get_type_matchup"}
    for s in specs:
        assert s["description"] and s["input_schema"]["properties"]
        assert s["kind"] in ("external", "local")
    assert len(list_specs("external")) >= 3


def test_unknown_tool_and_bad_argument_do_not_raise():
    assert call_tool("nope")["error"]["code"] == ErrorCode.UNKNOWN_TOOL
    assert call_tool("get_pokemon_detail", {})["error"]["code"] == ErrorCode.BAD_ARGUMENT
    assert call_tool("get_meta_usage", {"top_n": 0})["error"]["code"] == ErrorCode.BAD_ARGUMENT


# --- F3002 -----------------------------------------------------------------
def test_meta_default_values_are_disclosed(smogon_offline):
    r = call_tool("get_meta_usage", {})
    assert r["ok"] and r["data"]["mode"] == "ranking"
    assert r["data"]["month"] == "2026-10" and r["data"]["rating"] == 1500
    assert r["data"]["estimated"] is False
    assert any("기본값" in n for n in r["data"]["notes"])
    assert r["data"]["ranking"][0]["showdown_id"] == "garchomp"  # 다음 Tool 입력용 식별자


def test_meta_future_month_is_marked_estimated(smogon_offline):
    r = call_tool("get_meta_usage", {"pokemon": ["Garchomp"], "month": "2027-01"})
    assert r["ok"] and r["data"]["estimated"] is True and r["data"]["month"] == "2026-10"
    p = r["data"]["pokemon"][0]
    assert p["moves"][0] == {"name": "earthquake", "pct": 99.0}
    assert p["tera_types"] == []  # 'nothing'은 테라 없음 → 비워 둔다


def test_meta_unknown_pokemon_reported_not_invented(smogon_offline):
    r = call_tool("get_meta_usage", {"pokemon": ["Garchomp", "Pikachu"]})
    missing = r["data"]["pokemon"][1]
    assert missing["found"] is False and missing["reason"] == "NOT_IN_FORMAT"
    assert call_tool("get_meta_usage", {"pokemon": ["Pikachu"]})["error"]["code"] == ErrorCode.NO_DATA


def test_meta_nearest_rating_is_disclosed(smogon_offline):
    r = call_tool("get_meta_usage", {"rating": 1630})
    assert r["data"]["rating"] == 1500 and any("가장 가까운" in n for n in r["data"]["notes"])


# --- F3003 -----------------------------------------------------------------
def _pokemon(stats: dict, types=("dragon", "ground")):
    return {
        "name": "testmon",
        "types": [{"slot": i + 1, "type": {"name": t}} for i, t in enumerate(types)],
        "stats": [{"stat": {"name": k}, "base_stat": v} for k, v in stats.items()],
        "abilities": [],
        "species": {"url": "species-url"},
    }


def test_detail_missing_speed_is_not_judged(monkeypatch):
    def fake(url, **_):
        if url == "species-url":
            return {"names": [{"language": {"name": "ko"}, "name": "테스트몬"}]}
        return _pokemon({"hp": 100, "attack": 100})  # speed 없음

    monkeypatch.setattr(pokeapi, "http_get", fake)
    d = call_tool("get_pokemon_detail", {"name": "testmon"})["data"]
    assert d["judgeable"] is False and d["missing"] == ["spe"]
    assert d["base_stats"]["spe"] is None  # 임의 값으로 채우지 않음


def test_detail_falls_back_to_species_default_form(monkeypatch):
    def fake(url, **_):
        if url.endswith("/pokemon/mimikyu"):
            raise ToolError(ErrorCode.NOT_FOUND, "x")
        if url.endswith("/pokemon-species/mimikyu"):
            return {"varieties": [{"is_default": True, "pokemon": {"name": "mimikyu-disguised"}}]}
        if url == "species-url":
            return {"names": []}
        return _pokemon({"speed": 96}, types=("ghost", "fairy"))

    monkeypatch.setattr(pokeapi, "http_get", fake)
    d = call_tool("get_pokemon_detail", {"name": "Mimikyu"})["data"]
    assert d["types"] == ["Ghost", "Fairy"] and d["judgeable"] is True
    assert "mimikyu-disguised" in d["notes"][0]


def test_detail_upstream_error_is_returned(monkeypatch):
    def boom(url, **_):
        raise ToolError(ErrorCode.UPSTREAM_ERROR, "down")

    monkeypatch.setattr(pokeapi, "http_get", boom)
    assert call_tool("get_pokemon_detail", {"name": "Garchomp"})["error"]["code"] == ErrorCode.UPSTREAM_ERROR


# --- F3004 -----------------------------------------------------------------
TYPECHART_JS = (
    "exports.BattleTypeChart = {dragon:{damageTaken:{Ground:0,Ice:1,Electric:2,Fairy:1,Dragon:1}},"
    "ground:{damageTaken:{Ground:0,Ice:1,Electric:3,Fairy:0,Dragon:0}}};"
)


def test_type_matchup_and_ability(monkeypatch):
    monkeypatch.setattr(showdown, "http_get", lambda url, **_: TYPECHART_JS)
    r = call_tool("get_type_matchup", {"defend_types": ["드래곤", "땅"], "attack_types": ["얼음", "전기"]})
    m = {x["attack_type"]: x["multiplier"] for x in r["data"]["matchups"]}
    assert m == {"Ice": 4.0, "Electric": 0.0}
    r = call_tool("get_type_matchup", {"defend_types": ["Dragon"], "attack_types": ["Ground"], "defender_ability": "Levitate"})
    assert r["data"]["immune_to"] == ["Ground"]


def test_move_info_no_result_format(monkeypatch):
    monkeypatch.setattr(showdown, "http_get", lambda url, **_: {"earthquake": {"name": "Earthquake", "type": "Ground"}})
    r = call_tool("get_move_info", {"moves": ["earthquake", "notamove"]})
    assert r["ok"] and r["data"]["moves"][1] == {"input": "notamove", "found": False, "reason": "NOT_FOUND"}
    assert call_tool("get_move_info", {"moves": ["notamove"]})["error"]["code"] == ErrorCode.NOT_FOUND


# --- 로컬 툴 ---------------------------------------------------------------
def test_resolve_names_korean():
    r = call_tool("resolve_names", {"names": ["한카리아스", "메가한카리아스"]})
    assert [x["showdown_id"] for x in r["data"]["results"]] == ["garchomp", "garchompmega"]
    assert call_tool("resolve_names", {"names": ["없는포켓몬"]})["error"]["code"] == ErrorCode.NOT_FOUND


# --- 폼 이름 ----------------------------------------------------------------
@pytest.mark.parametrize("name,en,slug", [
    ("워시로토무", "Rotom-Wash", "rotom-wash"),
    ("스핀로토무", "Rotom-Fan", "rotom-fan"),
    ("연격우라오스", "Urshifu-Rapid-Strike", "urshifu-rapid-strike"),
    ("우물오거폰", "Ogerpon-Wellspring-Mask", "ogerpon-wellspring-mask"),
    ("로토무", "Rotom", "rotom"),
    ("알로라나인테일", "Ninetales-Alola", "ninetales-alola"),
])
def test_resolve_form_names(name, en, slug):
    r = call_tool("resolve_names", {"names": [name]})["data"]["results"][0]
    assert r["found"] and r["en"] == en and r["pokeapi_name"] == slug


def test_unknown_form_suggests_candidates():
    r = call_tool("resolve_names", {"names": ["이상한로토무", "한카리아스"]})["data"]["results"][0]
    assert r["reason"] == "AMBIGUOUS"
    assert {"label": "워시로토무", "value": "Rotom-Wash"} in r["candidates"]


@pytest.mark.parametrize("alias,en", [("물라오스", "Urshifu-Rapid-Strike"), ("악라오스", "Urshifu-Single-Strike"),
                                      ("물로토무", "Rotom-Wash"), ("불오거폰", "Ogerpon-Hearthflame-Mask")])
def test_aliases(alias, en):
    r = call_tool("resolve_names", {"names": [alias]})["data"]["results"][0]
    assert r["found"] and r["en"] == en


@pytest.mark.parametrize("typo,expected", [("물라오슨", "Urshifu-Rapid-Strike"), ("한카리야스", "Garchomp"),
                                           ("루카리온", "Lucario"), ("따라큐우", "Mimikyu"), ("Garchmop", "Garchomp")])
def test_typo_returns_suggestions(typo, expected):
    r = call_tool("resolve_names", {"names": [typo, "한카리아스"]})["data"]["results"][0]
    assert r["reason"] == "SUGGEST"
    assert r["candidates"][0]["value"] == expected  # 가장 비슷한 이름이 첫 후보
    assert len(r["candidates"]) <= 5


def test_meta_matches_smogon_form_spelling(monkeypatch):
    stats = {"info": {}, "data": {"Ogerpon": STATS["data"]["Garchomp"], "Ogerpon-Wellspring": STATS["data"]["Garchomp"],
                                  "Rotom-Wash": STATS["data"]["Garchomp"]}}
    monkeypatch.setattr(smogon, "http_get", lambda url, **_: stats if url.endswith(".json") else fake_smogon(url))
    d = call_tool("get_meta_usage", {"pokemon": ["Ogerpon-Wellspring-Mask", "워시로토무", "Rotom-Heat"]})["data"]
    found = [(p["input"], p.get("name")) for p in d["pokemon"]]
    assert found[0] == ("Ogerpon-Wellspring-Mask", "Ogerpon-Wellspring")
    assert found[1] == ("워시로토무", "Rotom-Wash")
    assert d["pokemon"][2]["found"] is False  # 다른 폼을 기본 종으로 잘못 붙이지 않음


# --- 메가진화 표기 ----------------------------------------------------------
@pytest.mark.parametrize("name,en", [
    ("메가한카리아스z", "Garchomp-Mega-Z"), ("메가한카리아스Z", "Garchomp-Mega-Z"), ("메가 한카리아스 Z", "Garchomp-Mega-Z"),
    ("한카리아스 메가Z", "Garchomp-Mega-Z"), ("메가앱솔z", "Absol-Mega-Z"), ("메가리자몽x", "Charizard-Mega-X"),
    ("메가보만다", "Salamence-Mega"),
])
def test_mega_names(name, en):
    r = call_tool("resolve_names", {"names": [name]})["data"]["results"][0]
    assert r["found"] and r["en"] == en and r["is_mega"] is True


def test_nonexistent_or_unclear_mega_is_not_invented():
    r = call_tool("resolve_names", {"names": ["메가고릴타", "메가리자몽", "한카리아스"]})["data"]["results"]
    assert r[0]["reason"] == "SUGGEST" and [c["value"] for c in r[0]["candidates"]] == ["Rillaboom"]
    assert {c["value"] for c in r[1]["candidates"]} == {"Charizard", "Charizard-Mega-X", "Charizard-Mega-Y"}
