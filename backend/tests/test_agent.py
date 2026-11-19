"""2일차: 메인 Agent 흐름·재계획·메모리·이벤트·로깅·계획/로그 API 테스트 (오프라인).

Tool은 가짜 데이터로, LLM은 FakeLLM으로 바꿔서 실행한다.
"""
from __future__ import annotations

import collections
import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.agent import executor as executor_mod
from app.agent import memory
from app.agent.graph import run_agent
from app.agent.llm import set_llm_factory
from app.events.bus import bus
from app.events.logging_agent import logging_agent
from app.storage.db import init_db
from tests.fake_llm import FakeLLM

# ---------------------------------------------------------------- 가짜 데이터
MULT = {  # 공격 타입 -> 방어 타입 -> 배율 (테스트용 4타입)
    "Water": {"Fire": 2, "Ground": 2, "Grass": 0.5, "Water": 0.5},
    "Fire": {"Grass": 2, "Water": 0.5, "Fire": 0.5, "Ground": 1},
    "Grass": {"Water": 2, "Ground": 2, "Fire": 0.5, "Grass": 0.5},
    "Ground": {"Fire": 2, "Grass": 0.5, "Water": 1, "Ground": 1},
}
POKEMON = {  # 이름: (타입, 스피드 종족값)
    "Aqua": (["Water"], 80), "Leafy": (["Grass"], 100), "Blaze": (["Fire"], 110),
    "Mud": (["Ground"], 50), "Ember": (["Fire"], 90), "Sprout": (["Grass"], 60), "Rocky": (["Ground"], 40), "Swift": (["Grass"], 200),
}
META = {  # 상대 실전 기술 (id, 비율)
    "ember": [("flamethrower", 90)], "sprout": [("energyball", 80)], "rocky": [("earthquake", 95)], "swift": [("energyball", 90)],
    "aqua": [("surf", 90)], "leafy": [("energyball", 90)], "blaze": [("flamethrower", 90)], "mud": [("earthquake", 90)],
}
MOVES = {"flamethrower": ("Fire", 90), "energyball": ("Grass", 90), "earthquake": ("Ground", 100), "surf": ("Water", 90)}


def fake_call_tool(tool: str, args: dict) -> dict:
    if tool == "resolve_names":
        res = []
        for n in args["names"]:
            if n == "모호몬":
                res.append({"input": n, "found": False, "reason": "AMBIGUOUS",
                            "candidates": [{"label": "엠버", "value": "Ember"}, {"label": "스프라웃", "value": "Sprout"}]})
            else:
                res.append({"input": n, "found": True, "en": n, "ko": None, "showdown_id": n.lower(), "pokeapi_name": n.lower()})
        return {"ok": True, "data": {"results": res}}
    if tool == "get_meta_usage":
        out = []
        for n in args["pokemon"]:
            mv = META.get(n.lower())
            out.append({"input": n, "found": True, "name": n, "showdown_id": n.lower(), "usage_pct": 10 + len(n),
                        "moves": [{"name": m, "pct": p} for m, p in mv], "items": [], "abilities": [], "spreads": []}
                       if mv else {"input": n, "found": False, "reason": "NOT_IN_FORMAT"})
        return {"ok": True, "data": {"format": "gen9ou", "month": "2026-10", "rating": 1500, "battles": 1,
                                     "estimated": False, "notes": [], "pokemon": out}}
    if tool == "get_pokemon_detail":
        if args["name"] == "Broken" or args["name"] not in POKEMON:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": "없음"}}
        types, spe = POKEMON[args["name"]]
        return {"ok": True, "data": {"name": args["name"].lower(), "ko_name": None, "types": types,
                                     "base_stats": {"spe": spe}, "abilities": [], "judgeable": True, "missing": []}}
    if tool == "get_move_info":
        ms = [{"input": m, "found": True, "id": m, "name": m, "ko_name": None, "type": MOVES[m][0],
               "category": "Special", "base_power": MOVES[m][1], "priority": 0} for m in args["moves"] if m in MOVES]
        return {"ok": True, "data": {"moves": ms}}
    if tool == "get_type_matchup":
        d = args["defend_types"][0]
        return {"ok": True, "data": {"matchups": [{"attack_type": a, "multiplier": MULT[a].get(d, 1)} for a in MULT]}}
    raise AssertionError(tool)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.sqlite3"))
    monkeypatch.setattr(executor_mod, "call_tool", fake_call_tool)
    init_db()
    memory._store.clear()
    yield
    set_llm_factory(None)
    logging_agent.detach()


def use_llm(*analyses, answer=None):
    fake = FakeLLM(list(analyses), answer)
    set_llm_factory(lambda: fake)
    return fake


def selection(**kw):
    base = {"intent": "plan_selection", "intent_reason": "선출 질문",
            "my_team": ["Aqua", "Leafy", "Blaze", "Mud"], "opp_team": ["Ember", "Sprout", "Rocky"]}
    return {**base, **kw}


def tool_calls(r):
    return [e["payload"] for e in r["events"] if e["type"] == "tool_call_end"]


# ---------------------------------------------------------------- F3005~F3011
def test_selection_flow_steps_and_draft():
    use_llm(selection(pick_count=2), answer="답변\n※ 사용률 통계 기반 추정이며, 상대의 실제 기술·도구·배분과 다를 수 있습니다.")
    r = run_agent("선출?")
    assert r["status"] == "ok" and r["intent"] == "plan_selection"
    steps = [e["step"] for e in r["events"] if e["type"] == "step_start"]
    assert steps == ["analyze", "plan", "execute", "judge", "respond"]
    assert r["plan_draft"]["selected"] and len(r["plan_draft"]["selected"]) == 2
    # 의도 분류 판단과 이유가 이벤트에 남는다
    d = next(e for e in r["events"] if e["type"] == "llm_decision")
    assert d["payload"]["intent"] == "plan_selection" and d["payload"]["reason"]


def test_no_tool_for_chitchat_and_ask():
    use_llm({"intent": "chitchat", "intent_reason": "인사"}, answer="안녕하세요")
    r = run_agent("안녕")
    assert r["status"] == "running" or r["answer"] == "안녕하세요"
    assert tool_calls(r) == []
    use_llm({"intent": "plan_selection", "intent_reason": "선출", "opp_team": ["Ember"]})
    r = run_agent("상대 팀만 줄게 뭐 뽑아?")
    assert r["status"] == "ask" and "내 포켓몬" in r["answer"] and tool_calls(r) == []


def test_replan_until_limit_without_recalling_tools():
    # Swift(풀, 스피드 200)를 2배로 때릴 수 있는 건 Blaze(불)뿐인데 제외 → 완화 기준(선공+등배)으로도 못 잡음
    use_llm(selection(opp_team=["Ember", "Swift"], must_answer=["Swift"], exclude=["Blaze"], pick_count=2))
    r = run_agent("Swift는 꼭 잡아야 해")
    assert r["status"] == "limit" and len(r["replans"]) == config.MAX_REPLAN
    assert [x["round"] for x in r["replans"]] == [1, 2, 3]
    calls = tool_calls(r)
    keys = [p["tool"] + json.dumps(p["args"], sort_keys=True) for p in calls]
    assert len(keys) == len(set(keys))  # 같은 Tool·같은 입력을 다시 부르지 않음
    detail = collections.Counter(p["args"]["name"] for p in calls if p["tool"] == "get_pokemon_detail")
    assert max(detail.values()) == 1
    assert "재계획을 3회" in r["answer"]


def test_replan_round1_extends_moves_and_succeeds():
    # Mud(땅)는 자속으로 Ember(불)만 2배 → 내 팀을 Mud, Blaze로 고정하면 Sprout(풀)은 Blaze가 잡음, Rocky는 못 잡음
    use_llm(selection(my_team=["Blaze", "Mud"], pick_count=2))
    r = run_agent("선출")
    assert r["replans"] and r["replans"][0]["round"] == 1
    replan_plan = [e for e in r["events"] if e["type"] == "plan"][1]["payload"]["steps"]
    assert replan_plan == ["meta_my", "moves_my"]  # 1회차는 내 실전 기술만 새로 조회


def test_tool_failure_does_not_stop_flow():
    use_llm(selection(my_team=["Aqua", "Leafy", "Blaze", "Broken"]))
    r = run_agent("선출")
    assert r["status"] in ("ok", "limit")
    assert any(not p["ok"] and p["error_code"] == "NOT_FOUND" for p in tool_calls(r))
    assert "Broken" not in r["plan_draft"]["selected"]


def test_ambiguous_name_asks_user():
    use_llm(selection(opp_team=["Ember", "모호몬"]))
    r = run_agent("선출")
    assert r["status"] == "ask" and "여러 포켓몬" in r["answer"]
    assert [p["tool"] for p in tool_calls(r)] == ["resolve_names"]  # 이름 확인 전에는 다른 Tool을 부르지 않음
    assert r["clarify"] == [{"input": "모호몬", "reason": "AMBIGUOUS",
                             "candidates": [{"label": "엠버", "value": "Ember"}, {"label": "스프라웃", "value": "Sprout"}]}]


def test_name_choice_is_applied_and_remembered():
    use_llm(selection(opp_team=["Sprout", "모호몬"]), selection(opp_team=["Sprout", "모호몬"]),
            selection(my_team=None, opp_team=None, exclude=["Blaze"]))
    r1 = run_agent("선출")
    assert r1["status"] == "ask"
    # 화면에서 후보를 고르면 같은 메시지를 name_choices와 함께 다시 보낸다
    r2 = run_agent("선출", r1["session_id"], {"모호몬": "Ember"})
    assert r2["status"] in ("ok", "limit") and r2["clarify"] == []
    resolve = next(p for p in tool_calls(r2) if p["tool"] == "resolve_names")
    assert "Ember" in resolve["args"]["names"] and "모호몬" not in resolve["args"]["names"]
    # 다음 요청에서도 세션이 고른 이름을 기억한다
    r3 = run_agent("Blaze 빼고", r1["session_id"])
    assert r3["status"] in ("ok", "limit")


def test_memory_updates_only_changed_conditions():
    use_llm(selection(), selection(my_team=None, opp_team=None, exclude=["Blaze"]))
    r1 = run_agent("선출")
    r2 = run_agent("Blaze 빼고 다시", r1["session_id"])
    assert r2["conditions"]["my_team"] == ["Aqua", "Leafy", "Blaze", "Mud"]  # 유지
    assert r2["conditions"]["exclude"] == ["Blaze"]
    assert "Blaze" not in r2["plan_draft"]["selected"]


def test_missing_llm_key_returns_guidance(monkeypatch):
    set_llm_factory(None)
    monkeypatch.setattr(config, "GMS_API_KEY", "")
    r = run_agent("선출")
    assert r["status"] == "error" and r["error"]["code"] == "INVALID_KEY"
    assert "GMS_API_KEY" in r["answer"]


def test_lookup_uses_only_needed_tools():
    use_llm({"intent": "lookup", "intent_reason": "종족값", "lookup_target": "Ember", "lookup_kind": "stats"})
    r = run_agent("Ember 종족값")
    assert {p["tool"] for p in tool_calls(r)} == {"resolve_names", "get_pokemon_detail"}


# ---------------------------------------------------------------- F3012~F3014 로깅
def test_main_agent_works_without_logging_agent_and_logs_by_session():
    logging_agent.detach()
    use_llm(selection())
    r = run_agent("선출")
    assert r["status"] in ("ok", "limit")
    from app.storage import logs
    assert logs.get_session(r["session_id"]) == []  # 구독자가 없어도 메인 Agent는 정상

    logging_agent.attach()
    use_llm(selection(), selection())
    a, b = run_agent("선출"), run_agent("선출")
    ea, eb = logs.get_session(a["session_id"]), logs.get_session(b["session_id"])
    assert len(ea) == len(a["events"]) and all(e["session_id"] == a["session_id"] for e in ea)
    assert [e["seq"] for e in ea] == sorted(e["seq"] for e in ea)
    assert {e["run_id"] for e in eb} == {b["run_id"]}


def test_broken_subscriber_does_not_break_agent():
    def boom(_):
        raise RuntimeError("logger down")
    bus.subscribe(boom)
    try:
        use_llm(selection())
        assert run_agent("선출")["answer"]
    finally:
        bus.unsubscribe(boom)


# ---------------------------------------------------------------- 계획 저장 (대화)
def test_save_plan_from_chat_with_overlap_notice():
    sched = {"date": "2026-11-21", "start_time": "15:00", "end_time": "15:30", "title": "1R"}
    use_llm(selection(), {"intent": "save_plan", "intent_reason": "저장", "schedule": sched},
            {"intent": "save_plan", "intent_reason": "저장", "schedule": {**sched, "start_time": "15:10", "end_time": "15:40"}})
    s = run_agent("선출")["session_id"]
    r1 = run_agent("토요일 15시~15시반 저장", s)
    assert r1["saved"]["plan"]["id"] and r1["saved"]["overlaps"] == []
    r2 = run_agent("하나 더 저장 15:10~15:40", s)
    assert r2["saved"]["overlaps"][0]["id"] == r1["saved"]["plan"]["id"] and "겹치는" in r2["answer"]


# ---------------------------------------------------------------- API
@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


PLAN = {"title": "대회 1R", "battle_date": "2026-11-21", "start_time": "15:00", "end_time": "15:30",
        "format": "gen9ou", "my_team": ["a"], "opponent_team": ["b"], "selected": ["a"], "lead": "a",
        "move_plan": [{"opponent": "b", "my_pokemon": "a", "move": "surf", "offense": 2, "speed": "확정 선공"}]}


def test_plans_crud_and_overlap(client):
    r = client.post("/api/plans", json=PLAN)
    assert r.status_code == 201 and r.json()["overlaps"] == []
    pid = r.json()["plan"]["id"]
    r2 = client.post("/api/plans", json={**PLAN, "title": "겹침", "start_time": "15:20", "end_time": "15:50"})
    assert r2.status_code == 201 and r2.json()["overlaps"][0]["id"] == pid  # 저장은 되고 겹침을 알림
    assert len(client.get("/api/plans").json()) == 2
    assert client.get("/api/plans?date=2026-11-22").json() == []
    u = client.put(f"/api/plans/{pid}", json={"start_time": "16:00", "end_time": "16:30"})
    assert u.json()["plan"]["start_time"] == "16:00" and u.json()["overlaps"] == []
    assert client.get(f"/api/plans/{pid}").json()["created_at"]
    assert client.delete(f"/api/plans/{pid}").status_code == 204
    assert client.get(f"/api/plans/{pid}").status_code == 404


def test_plan_validation(client):
    assert client.post("/api/plans", json={**PLAN, "end_time": "14:00"}).status_code == 422
    assert client.post("/api/plans", json={**PLAN, "battle_date": "11/21"}).status_code == 422


def test_logs_api_has_no_update(client):
    use_llm(selection())
    r = client.post("/api/chat", json={"message": "선출"})
    sid = r.json()["session_id"]
    assert r.status_code == 200 and r.json()["events"]
    assert any(s["session_id"] == sid for s in client.get("/api/logs").json())
    assert client.get(f"/api/logs/{sid}").json()[0]["seq"] == 1
    assert client.put(f"/api/logs/{sid}", json={}).status_code == 405
    assert client.patch(f"/api/logs/{sid}", json={}).status_code == 405
    assert client.delete(f"/api/logs/{sid}").status_code == 204
    assert client.get(f"/api/logs/{sid}").status_code == 404


# ---------------------------------------------------------------- 게임 규칙 (챔피언스 / 본가)
def test_ruleset_sets_format_and_pick():
    from app.domain.rulesets import RULESETS
    use_llm(selection(), selection())
    r = run_agent("선출", game="sv", battle="doubles")
    assert r["ruleset"]["format"] == RULESETS["sv"]["battles"]["doubles"]["format"]
    assert r["conditions"]["pick_count"] == 4 and r["conditions"]["game"] == "sv"
    r2 = run_agent("선출", r["session_id"])  # 다음 요청은 세션에 기억된 규칙을 쓴다
    assert r2["ruleset"]["game"] == "sv" and r2["ruleset"]["battle"] == "doubles"


def test_mega_not_allowed_in_sv(monkeypatch):
    def with_mega(tool, args):
        if tool == "resolve_names":
            res = fake_call_tool(tool, args)
            for item in res["data"]["results"]:
                if item["input"] == "Blaze":
                    item.update(en="Blaze-Mega", is_mega=True)
            return res
        return fake_call_tool(tool, args)
    monkeypatch.setattr(executor_mod, "call_tool", with_mega)
    monkeypatch.setattr(executor_mod, "resolve_one", lambda n, k: {"found": True, "en": n, "ko": None})
    use_llm(selection(), selection())
    r = run_agent("선출", game="sv")
    assert r["status"] == "ask" and r["clarify"][0]["reason"] == "NOT_IN_GAME"
    assert r["clarify"][0]["candidates"] == [{"label": "Blaze", "value": "Blaze"}]
    assert run_agent("선출", game="champions")["status"] in ("ok", "limit")  # 챔피언스는 메가 허용


# ---------------------------------------------------------------- 메가진화 1번 · 직접 선택 · 데미지
MEGAS = {"Aqua": [""], "Leafy": ["", "Z"], "Ember": [""]}      # 종 → 메가 폼 접미어
MEGA_USAGE = {"Aqua-Mega": 5.0, "Leafy-Mega": 2.0, "Leafy-Mega-Z": 8.0, "Ember-Mega": 30.0}
TEAM_FIT: dict = {}   # (폼, 팀원) → 함께 쓰인 비율(%)


@pytest.fixture
def mega_env(monkeypatch):
    from app.tools import names as N

    def tool(name, args):
        if name == "get_meta_usage":
            res = fake_call_tool(name, {**args, "pokemon": [p.split("-Mega")[0] for p in args["pokemon"]]})
            for p, entry in zip(args["pokemon"], res["data"]["pokemon"]):
                entry["input"] = p
                if "-Mega" in p:
                    entry.update(found=p in MEGA_USAGE, usage_pct=MEGA_USAGE.get(p, 0), name=p)
                if args.get("teammates") and entry.get("found"):
                    entry["team_fit"] = {t: TEAM_FIT.get((p, t), 0) for t in args["teammates"]}
            return res
        if name == "get_pokemon_detail" and "-Mega" in args["name"]:
            return fake_call_tool(name, {"name": args["name"].split("-Mega")[0]})
        return fake_call_tool(name, args)

    def variants(row):
        return [{"label": row["en"], "value": row["en"]}] + [
            {"label": f"메가{row['en']}{x}", "value": row["en"] + "-Mega" + (f"-{x}" if x else "")}
            for x in MEGAS.get(row["en"], [])]
    monkeypatch.setattr(executor_mod, "call_tool", tool)
    monkeypatch.setattr(N, "_species_variants", variants)
    monkeypatch.setattr(N, "resolve_one", lambda n, k: {"found": True, "en": n, "ko": None, "pokeapi_name": n.lower()})
    monkeypatch.setattr(N, "sprite_url", lambda s: None)


def test_asks_which_of_my_pokemon_mega_evolves(mega_env):
    use_llm(selection())
    r = run_agent("선출", game="champions")
    assert r["status"] == "ask" and "메가스톤" in r["answer"]
    opts = {o["pokemon"]: [f["value"] for f in o["forms"]] for o in r["mega"]["my_options"]}
    assert opts == {"Aqua": ["Aqua-Mega"], "Leafy": ["Leafy-Mega", "Leafy-Mega-Z"]}  # 메가 가능한 포켓몬만
    # 고르기 전엔 이름 확인 + 메가 폼 확인(기본 컷) + 상대 메가 예상(상위 컷·팀원) 까지만 부른다
    assert [p["tool"] for p in tool_calls(r)] == ["resolve_names", "get_meta_usage", "get_meta_usage"]
    hi = [p for p in tool_calls(r) if p["tool"] == "get_meta_usage"][1]["args"]
    assert hi["rating"] == 9999 and "Sprout" in hi["teammates"]
    pred = r["mega"]["opp_predictions"][0]
    assert pred["pokemon"] == "Ember" and pred["forms"][0]["share"] == 66.7  # 30 / (15 + 30)
    assert r["mega"]["opp_holders"] == {"Ember": "Ember-Mega"}  # 50% 이상 → 스톤 보유 예상


def test_several_stone_holders_and_best_mega_is_chosen(mega_env):
    use_llm(selection(), selection(my_team=None, opp_team=None, exclude=["Mud"]))
    holders = {"Aqua": "Aqua-Mega", "Leafy": "Leafy-Mega-Z"}   # 두 마리가 메가스톤을 든다
    r = run_agent("선출", game="champions", mega_choice={"my_holders": holders})
    assert r["status"] in ("ok", "limit") and r["conditions"]["my_megas"] == holders
    # 기본 폼과 메가 폼을 모두 계산했다
    for name in ("Aqua", "Aqua-Mega", "Leafy", "Leafy-Mega-Z"):
        assert name in r["pokemon_cards"]
    sel = r["result"]["selected"]
    assert sum(1 for x in sel if "-Mega" in x) <= 1                     # 배틀에서 메가진화는 1번
    if r["mega_evolve"]:
        assert r["mega_evolve"] in sel
    alts = r["mega_alternatives"]
    assert any(a["mega"] is None for a in alts) and sum(a["chosen"] for a in alts) == 1
    assert any("메가스톤" in n for n in r["rule_notes"])
    assert "Ember-Mega" in r["pokemon_cards"] and r["mega"]["opp_predicted"] is True  # 상대 메가 예상
    r2 = run_agent("Mud 빼고", r["session_id"])  # 기억된 보유자로 다시 계산 (다시 묻지 않음)
    assert r2["status"] in ("ok", "limit") and r2["conditions"]["my_megas"] == holders


def test_no_stone_holders_keeps_base_forms(mega_env):
    use_llm(selection())
    r = run_agent("선출", game="champions", mega_choice={"my_holders": {"Aqua": "none"}, "opp_holders": {}})
    assert r["status"] in ("ok", "limit") and r["mega_evolve"] is None
    assert not any("-Mega" in k for k in r["pokemon_cards"])


def test_opponent_mega_prediction_uses_team_fit(mega_env, monkeypatch):
    # 메가 폼이 이 상대 팀원들과는 거의 안 쓰이고, 기본 폼은 자주 쓰이면 → 메가 확률이 내려간다
    monkeypatch.setitem(TEAM_FIT, ("Ember", "Sprout"), 60.0)
    monkeypatch.setitem(TEAM_FIT, ("Ember", "Rocky"), 60.0)
    use_llm(selection())
    r = run_agent("선출", game="champions", mega_choice={"my_holders": {}})
    form = r["mega"]["opp_predictions"][0]["forms"][0]
    assert form["share"] == 66.7 and form["predicted"] < 50       # 통계 비율만으론 66.7%지만 팀 구성 반영 후 50% 미만
    assert r["mega"]["opp_holders"] == {}                         # 그래서 스톤 보유로 예상하지 않음


def test_several_opponent_holders_can_be_set(mega_env):
    use_llm(selection(opp_team=["Ember", "Sprout", "Rocky"]))
    r = run_agent("선출", game="champions", mega_choice={"my_holders": {}, "opp_holders": {"Ember": "Ember-Mega"}})
    assert r["mega"]["opp_predicted"] is False and "Ember-Mega" in r["pokemon_cards"]


def test_picked_teams_override_chat_analysis():
    use_llm({"intent": "chitchat", "intent_reason": "인사처럼 보임"})
    r = run_agent("이 팀으로 선출 추천해줘", my_team=["Aqua", "Leafy", "Blaze"], opp_team=["Ember", "Sprout"])
    assert r["intent"] == "plan_selection"
    assert r["conditions"]["my_team"] == ["Aqua", "Leafy", "Blaze"] and r["status"] in ("ok", "limit")


def test_move_plan_has_damage_when_meta_moves_known():
    use_llm(selection())
    r = run_agent("선출")
    with_damage = [p for p in r["move_plan"] if p.get("damage")]
    assert with_damage, "실전 기술(위력)이 있으면 데미지가 계산되어야 함"
    res = with_damage[0]["damage"]["results"][0]
    assert 0 < res["min_pct"] <= res["max_pct"] and res["ko"]
    assert "가정" in res["vs"] or "최다" in res["vs"]  # 어떤 배분 기준인지 밝힘


def test_pokemon_search_endpoint(client):
    rows = client.get("/api/chat/pokemon", params={"q": "한카", "game": "champions"}).json()
    labels = [r["label"] for r in rows]
    assert labels[:3] == ["한카리아스", "메가한카리아스", "메가한카리아스Z"] and rows[0]["sprite"].endswith("/445.png")
    sv = [r["label"] for r in client.get("/api/chat/pokemon", params={"q": "한카", "game": "sv"}).json()]
    assert sv == ["한카리아스"]  # 본가는 메가 제외
