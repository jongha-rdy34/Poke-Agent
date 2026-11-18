"""[외부 API 툴] F3003 상세정보 조회 — PokeAPI (pokeapi.co).

후보의 식별자로 타입·종족값·특성을 조회한다. 선공/상성 판단에 쓰는 값이다.
판단에 필요한 값(타입, 스피드 종족값)이 비어 있으면 임의로 채우지 않고
judgeable=False와 missing 목록을 돌려준다.
"""
from __future__ import annotations

from app import config
from app.tools.common import ErrorCode, ToolError, http_get, ok, safe_tool
from app.tools.names import resolve_one

TTL = 7 * 24 * 3600
STAT_KEYS = {"hp": "hp", "attack": "atk", "defense": "def",
             "special-attack": "spa", "special-defense": "spd", "speed": "spe"}
REQUIRED = ("types", "spe")


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "-").replace(".", "").replace("'", "").replace(":", "")


def _fetch_pokemon(slug: str) -> tuple[dict, list[str]]:
    """/pokemon/{slug} 조회. 없으면 /pokemon-species/{slug}의 기본 폼으로 보정한다.

    Smogon은 'Mimikyu', PokeAPI는 'mimikyu-disguised'처럼 이름 규칙이 달라서 필요하다.
    """
    notes: list[str] = []
    try:
        return http_get(f"{config.POKEAPI_BASE}/pokemon/{slug}", ttl=TTL), notes
    except ToolError as e:
        if e.code != ErrorCode.NOT_FOUND:
            raise
    species = http_get(f"{config.POKEAPI_BASE}/pokemon-species/{slug}", ttl=TTL)  # 없으면 NOT_FOUND
    default = next((v["pokemon"]["name"] for v in species.get("varieties", []) if v.get("is_default")), None)
    if not default:
        raise ToolError(ErrorCode.NO_DATA, f"'{slug}' 종의 기본 폼 정보가 없습니다.")
    notes.append(f"'{slug}' 폼이 따로 없어 기본 폼 '{default}'으로 조회했습니다.")
    return http_get(f"{config.POKEAPI_BASE}/pokemon/{default}", ttl=TTL), notes


@safe_tool
def get_pokemon_detail(name: str) -> dict:
    if not name or not name.strip():
        raise ToolError(ErrorCode.BAD_ARGUMENT, "name이 비어 있습니다.")

    notes: list[str] = []
    slug = _slug(name)
    if not name.isascii():  # 한국어 이름 → 영문 식별자
        r = resolve_one(name, "pokemon")
        if not r["found"]:
            code = ErrorCode.AMBIGUOUS if r["reason"] == "AMBIGUOUS" else ErrorCode.NOT_FOUND
            raise ToolError(code, f"'{name}'에 해당하는 포켓몬을 찾지 못했습니다.")
        slug = r["pokeapi_name"]

    p, fetch_notes = _fetch_pokemon(slug)
    notes += fetch_notes

    types = [t["type"]["name"].capitalize() for t in sorted(p.get("types", []), key=lambda t: t["slot"])]
    stats = {STAT_KEYS[s["stat"]["name"]]: s.get("base_stat") for s in p.get("stats", []) if s["stat"]["name"] in STAT_KEYS}
    abilities = [{"name": a["ability"]["name"], "hidden": a.get("is_hidden", False)} for a in p.get("abilities", [])]

    ko_name = None
    try:
        species = http_get(p["species"]["url"], ttl=TTL)
        ko_name = next((n["name"] for n in species.get("names", []) if n["language"]["name"] == "ko"), None)
    except ToolError:
        notes.append("한국어 이름을 가져오지 못했습니다 (판단에는 영향 없음).")

    missing = [k for k in REQUIRED if (k == "types" and not types) or (k != "types" and stats.get(k) is None)]
    return ok({
        "name": p["name"],
        "ko_name": ko_name,
        "types": types,
        "base_stats": {k: stats.get(k) for k in ("hp", "atk", "def", "spa", "spd", "spe")},
        "abilities": abilities,
        "judgeable": not missing,
        "missing": missing,
        "notes": notes,
        "source": "PokeAPI",
    })
