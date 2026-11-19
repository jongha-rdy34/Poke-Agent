"""게임 버전별 규칙. 화면 첫 토글에서 고르고, 대화 세션 조건(format, pick_count, game)으로 들어간다.

- champions: 포켓몬 챔피언스 — 메가진화 룰, 테라스탈 없음, 능력치는 SP(스탯당 최대 32)
- sv: 포켓몬 스칼렛·바이올렛(본가) — 테라스탈 룰, 메가진화 없음, 노력치(최대 252). 랭크 시즌 종료 → 마지막 집계 통계 사용
"""
from __future__ import annotations

RULESETS: dict[str, dict] = {
    "champions": {
        "label": "포켓몬 챔피언스",
        "gimmick": "메가진화",
        "allow_mega": True,
        "allow_tera": False,
        "stat_system": "SP(스탯당 최대 32)",
        "status": "진행 중인 시즌",
        "battles": {
            "singles": {"label": "싱글 (6→3 선출)", "format": "gen9championsbssregmc", "pick": 3},
            "doubles": {"label": "더블 (6→4 선출)", "format": "gen9championsvgc2026regmc", "pick": 4},
        },
    },
    "sv": {
        "label": "스칼렛·바이올렛 (본가)",
        "gimmick": "테라스탈",
        "allow_mega": False,
        "allow_tera": True,
        "stat_system": "노력치(최대 252)",
        "status": "종료된 시즌 — 마지막 집계 통계 기준",
        "battles": {
            "singles": {"label": "싱글 (6→3 선출)", "format": "gen9bssregi", "pick": 3},
            "doubles": {"label": "더블 (6→4 선출)", "format": "gen9vgc2026regi", "pick": 4},
        },
    },
}
DEFAULT_GAME = "champions"
DEFAULT_BATTLE = "singles"


def resolve(game: str | None, battle: str | None) -> dict:
    g = game if game in RULESETS else DEFAULT_GAME
    b = battle if battle in RULESETS[g]["battles"] else DEFAULT_BATTLE
    rs = RULESETS[g]
    bt = rs["battles"][b]
    return {"game": g, "battle": b, "game_label": rs["label"], "battle_label": bt["label"],
            "format": bt["format"], "pick": bt["pick"], "gimmick": rs["gimmick"],
            "allow_mega": rs["allow_mega"], "allow_tera": rs["allow_tera"],
            "stat_system": rs["stat_system"], "status": rs["status"]}


def public_list() -> list[dict]:
    return [{"id": g, **{k: v for k, v in rs.items() if k != "battles"},
             "battles": [{"id": b, **bt} for b, bt in rs["battles"].items()]} for g, rs in RULESETS.items()]
