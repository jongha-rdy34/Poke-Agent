"""1일차 체크리스트 1~4: Tool을 Agent 없이 단독 실행해 정상 1회 + 실패 2가지를 확인한다.

실행: python run_tools.py            (backend 폴더에서)
      python run_tools.py --save     (결과를 docs/verification/day1_tool_run.json 으로 저장)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app import config
from app.tools import call_tool, list_specs
from app.tools import common

CASES = [
    # (설명, tool, args)
    ("[등록] -", None, None),
    ("[F3002 정상] 기본값(포맷·월 미지정) 사용률 상위 후보", "get_meta_usage", {"top_n": 6}),
    ("[F3002 정상] 상대 포켓몬 예상 세트 (한국어 이름 포함)", "get_meta_usage", {"pokemon": ["Garchomp", "따라큐", "Primarina"], "top_n": 4}),
    ("[F3002 추정] 아직 없는 미래 월 요청 → 최근 집계월로 추정", "get_meta_usage", {"pokemon": ["Garchomp"], "month": "2026-12", "top_n": 3}),
    ("[F3002 실패] 형식이 틀린 월", "get_meta_usage", {"month": "12월"}),
    ("[F3002 실패] 없는 포맷", "get_meta_usage", {"format": "gen9notaformat"}),
    ("[F3003 정상] 상세정보 (이름 규칙 보정: Mimikyu → mimikyu-disguised)", "get_pokemon_detail", {"name": "Mimikyu"}),
    ("[F3003 정상] 한국어 이름", "get_pokemon_detail", {"name": "한카리아스"}),
    ("[F3003 실패] 빈 입력", "get_pokemon_detail", {"name": ""}),
    ("[F3003 실패] 없는 포켓몬", "get_pokemon_detail", {"name": "Notamon"}),
    ("[F3004 정상] 기술 정보 (Smogon 기술 ID 그대로 입력)", "get_move_info", {"moves": ["earthquake", "suckerpunch", "기습"]}),
    ("[F3004 정상] 타입 상성 + 특성 면역", "get_type_matchup", {"defend_types": ["Dragon"], "attack_types": ["Ground", "Ice", "Fairy"], "defender_ability": "Levitate"}),
    ("[F3004 결과 없음] 없는 기술만 요청", "get_move_info", {"moves": ["notamove"]}),
    ("[F3004 실패] 없는 타입", "get_type_matchup", {"defend_types": ["Light"]}),
    ("[로컬] 이름 변환", "resolve_names", {"names": ["한카리아스", "메가한카리아스", "누구지"]}),
    ("[공통 실패] 등록되지 않은 Tool", "no_such_tool", {}),
]


def run_upstream_down() -> list[dict]:
    """외부 서버 장애(키 없음/연결 불가에 해당) 시에도 예외 없이 실패 응답이 나오는지 확인."""
    saved = (config.POKEAPI_BASE, config.SMOGON_STATS_BASE, config.SHOWDOWN_DATA_BASE)
    config.POKEAPI_BASE = config.SMOGON_STATS_BASE = config.SHOWDOWN_DATA_BASE = "http://127.0.0.1:9"
    common._mem_cache.clear()
    try:
        return [
            {"case": f"[{t} 실패] 외부 서버 연결 불가", "tool": t, "args": a, "result": call_tool(t, a)}
            for t, a in [
                ("get_meta_usage", {}),
                ("get_pokemon_detail", {"name": "Garchomp"}),
                ("get_move_info", {"moves": ["earthquake"]}),
            ]
        ]
    finally:
        config.POKEAPI_BASE, config.SMOGON_STATS_BASE, config.SHOWDOWN_DATA_BASE = saved
        common._mem_cache.clear()


def main() -> None:
    out: list[dict] = []
    specs = list_specs()
    print("=" * 80)
    print(f"등록된 Tool {len(specs)}개")
    for s in specs:
        print(f"  - {s['name']:<20} [{s['kind']:<8}] {s['requirement']}  {s['source']}")
        print(f"      입력: {list(s['input_schema']['properties'])}")
    out.append({"case": "등록된 Tool 명세", "specs": specs})

    for desc, tool, args in CASES[1:]:
        result = call_tool(tool, args)
        out.append({"case": desc, "tool": tool, "args": args, "result": result})
        status = "OK  " if result["ok"] else f"FAIL {result['error']['code']}"
        print("-" * 80)
        print(f"{desc}\n  {tool}({json.dumps(args, ensure_ascii=False)}) -> {status}")
        print("  " + json.dumps(result, ensure_ascii=False)[:400])

    for row in run_upstream_down():
        out.append(row)
        r = row["result"]
        print("-" * 80)
        print(f"{row['case']} -> {'OK' if r['ok'] else 'FAIL ' + r['error']['code']}: {r.get('error', {}).get('message')}")

    if "--save" in sys.argv:
        path = Path(__file__).resolve().parents[1] / "docs" / "verification" / "day1_tool_run.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {path}")


if __name__ == "__main__":
    main()
