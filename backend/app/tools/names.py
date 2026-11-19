"""[로컬 서버 API 툴] 한국어/영어 이름 → 식별자 변환.

외부 API 세 곳은 영문 식별자만 받는다. 사용자는 '한카리아스', '지진'처럼 한국어로 말하므로
PokeAPI에서 한 번 수집해 둔 매핑 파일(app/data/name_map.json)로 변환한다.
매핑 파일은 scripts/build_name_map.py로 다시 만들 수 있다.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from app.tools.common import ErrorCode, ToolError, ok, safe_tool, to_id

MAP_PATH = Path(__file__).resolve().parents[1] / "data" / "name_map.json"

# 한국어 폼 접두/접미어 → Showdown 폼 접미어
FORM_PREFIXES = {"메가": "-Mega", "알로라": "-Alola", "가라르": "-Galar", "히스이": "-Hisui", "팔데아": "-Paldea"}


# 폼 설명에서 떼어낼 꼬리말: '연격의 태세' -> '연격', '우물의가면' -> '우물', '백마 탄 모습' -> '백마'
FORM_TAILS = ("의 모습", "의모습", "의 태세", "의태세", "의가면", "의 가면", " 탄 모습", "탄모습", " 모습", "모습", " 폼", "폼")


def _form_short(ko_form: str) -> str:
    for tail in FORM_TAILS:
        if ko_form.endswith(tail) and len(ko_form) > len(tail):
            return ko_form[: -len(tail)].replace(" ", "")
    return ko_form.replace(" ", "")


def _add(index: dict, key: str, row: dict) -> None:
    if not key:
        return
    bucket = index.setdefault(key, [])
    if row not in bucket:
        bucket.append(row)


@lru_cache(maxsize=1)
def _load() -> dict:
    if not MAP_PATH.exists():
        raise ToolError(ErrorCode.NO_DATA, "이름 매핑 파일이 없습니다. scripts/build_name_map.py를 먼저 실행하세요.")
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    index: dict[str, dict[str, list[dict]]] = {"pokemon": {}, "move": {}}
    for kind, rows in (("pokemon", data["species"]), ("move", data["moves"])):
        for row in rows:
            for k in {row["id"], to_id(row["slug"]), (row.get("ko") or "").replace(" ", "")}:
                _add(index[kind], k, row)

    # 폼: '워시로토무'(rotom-wash), '연격우라오스'(urshifu-rapid-strike), '우물오거폰'(ogerpon-wellspring-mask) …
    species_ko = {r["slug"]: r["ko"] for r in data["species"] if r.get("ko")}
    forms_by_species: dict[str, list[dict]] = {}
    for f in data.get("forms", []):
        sp_ko = species_ko.get(f["species_slug"])
        ko_form = (f.get("ko") or f.get("ko_form") or "").replace(" ", "")
        if not sp_ko or not ko_form:
            continue
        display = ko_form if sp_ko in ko_form else f"{_form_short(ko_form)}{sp_ko}"
        row = {"slug": f["slug"], "en": f["en"], "id": f["id"], "ko": display, "form": True}
        forms_by_species.setdefault(sp_ko, []).append(row)
        for k in {f["id"], to_id(f["slug"]), display, ko_form + sp_ko, sp_ko + _form_short(ko_form)}:
            _add(index["pokemon"], k, row)
    index["_forms_by_species"] = forms_by_species
    return index


def _pick_form(hits: list[dict]) -> list[dict]:
    """같은 이름에 폼이 여럿이면(지가르데-10 / 지가르데-10-스웜체인지) 이름이 가장 짧은 기본 폼을 고른다."""
    if len(hits) > 1 and all(h.get("form") for h in hits):
        shortest = min(hits, key=lambda h: len(h["slug"]))
        if all(h["slug"].startswith(shortest["slug"]) for h in hits):
            return [shortest]
    return hits


def _form_candidates(compact: str) -> list[dict]:
    """'워시로토무'를 못 찾았을 때처럼 이름 안에 종 이름이 들어 있으면 그 종의 폼을 후보로 돌려준다."""
    forms = _load()["_forms_by_species"]
    matches = [sp for sp in forms if sp in compact and sp != compact]
    if not matches:
        return []
    sp = max(matches, key=len)
    return forms[sp]


def _split_form(name: str) -> tuple[str, str]:
    """'메가한카리아스' -> ('한카리아스', '-Mega'). 폼 표기가 없으면 접미어는 ''."""
    compact = name.replace(" ", "")
    for ko, suffix in FORM_PREFIXES.items():
        if compact.startswith(ko) and len(compact) > len(ko):
            return compact[len(ko):], suffix
    return compact, ""


def resolve_one(name: str, kind: Literal["pokemon", "move"]) -> dict:
    """이름 하나를 변환. 찾지 못하면 found=False로 돌려준다 (예외 없음)."""
    index = _load()[kind]
    raw = name.strip()
    compact = raw.replace(" ", "")
    hits = index.get(compact) or index.get(to_id(compact)) or [] if kind == "pokemon" else []
    suffix = ""
    if hits:
        hits = _pick_form(hits)
    else:
        base, suffix = _split_form(raw) if kind == "pokemon" else (compact, "")
        hits = index.get(base) or index.get(to_id(base)) or []
    if not hits and kind == "pokemon" and "-" in raw:
        # 'Garchomp-Mega-Z', 'Indeedee-F' 처럼 영문 폼 표기는 앞부분이 종 이름
        head, _, tail = raw.partition("-")
        hits = index.get(to_id(head)) or []
        suffix = "-" + tail if hits else ""

    if not hits:
        cands = _form_candidates(compact) if kind == "pokemon" and not raw.isascii() else []
        if cands:
            return {"input": name, "found": False, "reason": "AMBIGUOUS",
                    "candidates": [f"{c['ko']}({c['en']})" for c in cands]}
        return {"input": name, "found": False, "reason": "NOT_FOUND"}
    if len(hits) > 1:
        return {"input": name, "found": False, "reason": "AMBIGUOUS",
                "candidates": [f"{h['ko']}({h['en']})" if h.get("ko") else h["en"] for h in hits]}

    row = hits[0]
    en = row["en"] + suffix
    if row.get("form"):
        ko = row["ko"]
    else:
        ko = (FORM_KO.get(suffix, "") + row["ko"]) if row.get("ko") else None
    result = {
        "input": name,
        "found": True,
        "en": en,
        "ko": ko,
        "showdown_id": to_id(en),          # get_meta_usage / get_move_info 입력
    }
    if kind == "pokemon":
        result["pokeapi_name"] = row["slug"] if row.get("form") else en.lower().replace(" ", "-").replace(".", "").replace("'", "").replace(":", "")
    return result


FORM_KO = {v: k for k, v in FORM_PREFIXES.items()}


@safe_tool
def resolve_names(names: list[str], kind: Literal["pokemon", "move"] = "pokemon") -> dict:
    if not names or any(not str(n).strip() for n in names):
        raise ToolError(ErrorCode.BAD_ARGUMENT, "names에 빈 이름이 있습니다.")
    results = [resolve_one(n, kind) for n in names]
    if not any(r["found"] for r in results):
        if any(r.get("reason") == "AMBIGUOUS" for r in results):
            raise ToolError(ErrorCode.AMBIGUOUS, "이름이 여러 대상에 해당합니다. 정확한 이름을 다시 물어보세요.")
        raise ToolError(ErrorCode.NOT_FOUND, f"이름을 찾지 못했습니다: {', '.join(names)}")
    return ok({"kind": kind, "results": results, "source": "로컬 매핑 (PokeAPI 수집본)"})
