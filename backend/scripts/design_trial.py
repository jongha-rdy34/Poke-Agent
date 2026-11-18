"""기획 검증 '설계 시험': 대표 요청 하나를 설계한 Tool 호출 순서대로 따라가며
각 Tool의 입력이 요청 분석 결과나 앞선 Tool 결과로 모두 채워지는지 확인한다.
(LLM 없이 규칙으로 흉내 낸 2일차 판단 단계의 초안)

실행: python scripts/design_trial.py   (backend 폴더에서)
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.tools import call_tool  # noqa: E402

# 요청 분석 결과(가정): "상대 팀 보고 내 6마리 중 3마리 뽑아줘. 상대 메가한카는 꼭 잡고 싶어"
MY_TEAM = ["하마돈", "마스카나", "고릴타", "대쓰여너", "보만다", "루카리오"]
OPP_TEAM = ["Primarina", "Garchomp", "Archaludon", "Gholdengo", "Mimikyu", "Corviknight"]
PICK = 3
log = []


def step(name, args):
    r = call_tool(name, args)
    log.append({"tool": name, "args": args, "ok": r["ok"], "error": r.get("error")})
    return r


# 1) 이름 변환 (로컬)
names = step("resolve_names", {"names": MY_TEAM})["data"]["results"]
my_ids = [n["en"] for n in names if n["found"]]
# 2) 상대 예상 세트 (F3002) — 입력: 상대 팀 이름
meta = step("get_meta_usage", {"pokemon": OPP_TEAM, "top_n": 6})["data"]
# 3) 상세정보 (F3003) — 입력: 1)의 en 이름 + 상대 이름
detail = {}
for n in my_ids + OPP_TEAM:
    r = step("get_pokemon_detail", {"name": n})
    if r["ok"] and r["data"]["judgeable"]:
        detail[n] = r["data"]
# 4) 상대 주력 기술 정보 (F3004) — 입력: 2)의 기술 ID
opp_moves = {}
for p in meta["pokemon"]:
    if not p["found"]:
        continue
    mv = step("get_move_info", {"moves": [m["name"] for m in p["moves"]]})["data"]["moves"]
    opp_moves[p["input"]] = [m for m in mv if m["found"] and m["category"] != "Status"]


def mult(atk_type, defender):
    d = detail[defender]
    r = step("get_type_matchup", {"defend_types": d["types"], "attack_types": [atk_type],
                                  "defender_ability": d["abilities"][0]["name"] if len(d["abilities"]) == 1 else None})
    return r["data"]["matchups"][0]["multiplier"]


# 5) 판단(코드): 내 포켓몬 m 이 상대 o 를 '잡는다' = 자속 2배 이상 & o의 주력 공격기에 2배 이상 맞지 않음
answers = {}
for m in my_ids:
    for o in OPP_TEAM:
        if m not in detail or o not in detail:
            continue
        off = max(mult(t, o) for t in detail[m]["types"])
        dmg_in = max([mult(x["type"], m) for x in opp_moves.get(o, [])] or [1])
        answers[(m, o)] = {"offense": off, "risk": dmg_in, "faster": detail[m]["base_stats"]["spe"] > detail[o]["base_stats"]["spe"]}
covers = {m: [o for o in OPP_TEAM if (m, o) in answers and answers[(m, o)]["offense"] >= 2 and answers[(m, o)]["risk"] < 2] for m in my_ids}

best = max(itertools.combinations(my_ids, PICK), key=lambda c: len(set().union(*[covers[m] for m in c])))
covered = set().union(*[covers[m] for m in best])
print(json.dumps({
    "meta_basis": {k: meta[k] for k in ("format", "month", "rating", "estimated", "notes")},
    "covers": covers,
    "best_pick": best,
    "uncovered": [o for o in OPP_TEAM if o not in covered],
    "replan_needed": len(covered) < len(OPP_TEAM),
    "tool_calls": len(log),
    "failed_calls": [l for l in log if not l["ok"]],
}, ensure_ascii=False, indent=2))
