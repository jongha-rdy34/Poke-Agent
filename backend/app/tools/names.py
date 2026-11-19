"""[로컬 서버 API 툴] 한국어/영어 이름 → 식별자 변환.

외부 API 세 곳은 영문 식별자만 받는다. 사용자는 '한카리아스', '지진'처럼 한국어로 말하므로
PokeAPI에서 한 번 수집해 둔 매핑 파일(app/data/name_map.json)로 변환한다.
매핑 파일은 scripts/build_name_map.py로 다시 만들 수 있다.
"""
from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from app.tools.common import ErrorCode, ToolError, ok, safe_tool, to_id

MAP_PATH = Path(__file__).resolve().parents[1] / "data" / "name_map.json"
ALIAS_PATH = Path(__file__).resolve().parents[1] / "data" / "aliases.json"  # 별명 사전 (물라오스, 악라오스 …)
SUGGEST_CUTOFF = 0.6   # 자모 유사도 이 값 이상만 후보로
MAX_SUGGEST = 5

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
    index["_slugs"] = set(data.get("pokemon_slugs") or [])
    index["_ids"] = data.get("pokemon_ids") or {}

    # 별명: 대상 이름으로 찾은 항목을 별명 키에도 걸어 둔다
    if ALIAS_PATH.exists():
        aliases = json.loads(ALIAS_PATH.read_text(encoding="utf-8"))
        for alias, target in aliases.items():
            if alias.startswith("_"):
                continue
            rows = index["pokemon"].get(to_id(target))
            if not rows:
                head, _, tail = target.partition("-")
                base = index["pokemon"].get(to_id(head))
                rows = [{**base[0], "en": target, "slug": target.lower(), "ko": alias, "form": True}] if base and tail else None
            if rows:
                _add(index["pokemon"], alias.replace(" ", ""), _pick_form(rows)[0])

    # 비슷한 이름 후보용: 한국어 키 → (자모열, 대표 항목)
    index["_suggest"] = {
        kind: [(k, _jamo(k), _pick_form(rows)[0]) for k, rows in index[kind].items()
               if k and not k.isascii() and len(_pick_form(rows)) == 1]
        for kind in ("pokemon", "move")
    }
    return index


def _jamo(text: str) -> str:
    """한글 음절을 자모로 풀어 비교한다: '라오스' vs '라우스'처럼 한 글자만 틀려도 비슷하게 잡힌다."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out += [chr(0x1100 + code // 588), chr(0x1161 + (code % 588) // 28)]
            if code % 28:
                out.append(chr(0x11A7 + code % 28))
        else:
            out.append(ch.lower())
    return "".join(out)


def _candidate(row: dict) -> dict:
    """화면에서 고를 수 있는 후보: label(보여줄 이름), value(다시 넣을 영문 이름)."""
    return {"label": row.get("ko") or row["en"], "value": row["en"]}


def suggest(name: str, kind: Literal["pokemon", "move"] = "pokemon") -> list[dict]:
    """정확히 일치하지 않는 이름에 대해 비슷한 이름 후보를 돌려준다."""
    compact = name.replace(" ", "")
    if compact.isascii():
        index = _load()[kind]
        keys = difflib.get_close_matches(to_id(compact), [k for k in index if k.isascii()], n=MAX_SUGGEST, cutoff=0.75)
        rows = [_pick_form(index[k])[0] for k in keys]
    else:
        target = _jamo(compact)
        scored = []
        for key, jamo, row in _load()["_suggest"][kind]:
            ratio = difflib.SequenceMatcher(None, target, jamo).ratio()
            if ratio >= SUGGEST_CUTOFF:
                scored.append((ratio, key, row))
        scored.sort(key=lambda x: -x[0])
        rows = [r for _, _, r in scored]
    out, seen = [], set()
    for r in rows:
        c = _candidate(r)
        if c["value"] not in seen:
            seen.add(c["value"])
            out.append(c)
        if len(out) >= MAX_SUGGEST:
            break
    return out


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
    # 폼 이름은 '워시로토무'처럼 종 이름 앞에 붙는다 → 종 이름으로 끝날 때만 (오타 '따라큐우'는 제외)
    matches = [sp for sp in forms if compact.endswith(sp) and sp != compact]
    if not matches:
        return []
    sp = max(matches, key=len)
    return forms[sp]


_MEGA_LETTER = re.compile(r"^(?P<base>.+?)(?P<letter>[XYZxyzＸＹＺｘｙｚ])$")
_FULLWIDTH = str.maketrans("ＸＹＺｘｙｚ", "XYZxyz")


def _split_form(name: str) -> tuple[str, str]:
    """'메가한카리아스' -> ('한카리아스', '-Mega'), '메가한카리아스z' -> ('한카리아스', '-Mega-Z'),
    '한카리아스 메가Z' -> ('한카리아스', '-Mega-Z'). 폼 표기가 없으면 접미어는 ''."""
    compact = name.replace(" ", "").translate(_FULLWIDTH)
    if compact.startswith("메가") and len(compact) > 2:
        base, suffix = compact[2:], "-Mega"
    elif "메가" in compact[1:]:  # 이름 뒤에 메가를 붙인 경우
        base, _, tail = compact.partition("메가")
        if tail and not _MEGA_LETTER.fullmatch("_" + tail):
            return compact, ""
        base, suffix = base, "-Mega" + (f"-{tail.upper()}" if tail else "")
        return base, suffix
    else:
        for ko, sfx in FORM_PREFIXES.items():
            if ko != "메가" and compact.startswith(ko) and len(compact) > len(ko):
                return compact[len(ko):], sfx
        return compact, ""
    m = _MEGA_LETTER.fullmatch(base)
    if m and base not in _load()["pokemon"]:  # '한카리아스z' → 한카리아스 + 메가 Z
        return m["base"], f"-Mega-{m['letter'].upper()}"
    return base, suffix


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
        forms = _form_candidates(compact) if kind == "pokemon" and not raw.isascii() else []
        if forms:  # 종 이름은 들어 있는데 폼을 모름 → 그 종의 폼 중에서 고르게 한다
            return {"input": name, "found": False, "reason": "AMBIGUOUS", "candidates": [_candidate(c) for c in forms]}
        cands = suggest(raw, kind)
        if cands:  # 비슷한 이름이 있음 → 맞는지 되묻는다
            return {"input": name, "found": False, "reason": "SUGGEST", "candidates": cands}
        return {"input": name, "found": False, "reason": "NOT_FOUND", "candidates": []}
    if len(hits) > 1:
        return {"input": name, "found": False, "reason": "AMBIGUOUS", "candidates": [_candidate(h) for h in hits]}

    row = hits[0]
    en = row["en"] + suffix
    if row.get("form"):
        ko = row["ko"]
    elif not row.get("ko"):
        ko = None
    elif suffix in FORM_KO:
        ko = FORM_KO[suffix] + row["ko"]
    elif suffix.startswith("-Mega"):  # '-Mega-Z' -> '메가한카리아스Z'
        ko = "메가" + row["ko"] + suffix[5:].replace("-", "")
    elif suffix:  # 그 밖의 영문 폼 표기는 종 이름과 구분되게 붙여 둔다
        ko = f"{row['ko']}({suffix[1:]})"
    else:
        ko = row["ko"]
    result = {
        "input": name,
        "found": True,
        "en": en,
        "ko": ko,
        "showdown_id": to_id(en),          # get_meta_usage / get_move_info 입력
    }
    if kind == "pokemon":
        result["pokeapi_name"] = row["slug"] if row.get("form") else en.lower().replace(" ", "-").replace(".", "").replace("'", "").replace(":", "")
        if suffix and not row.get("form") and not _exists(result["pokeapi_name"]):
            # '메가고릴타'처럼 존재하지 않는 폼은 지어내지 않고, 그 종의 실제 폼(기본·메가)을 후보로 준다
            return {"input": name, "found": False, "reason": "SUGGEST", "candidates": _species_variants(row)}
        result["is_mega"] = "-mega" in result["pokeapi_name"]
    return result


def _exists(slug: str) -> bool:
    slugs = _load().get("_slugs")
    return True if not slugs else slug in slugs


def _species_variants(row: dict) -> list[dict]:
    """기본 종 + 실제로 존재하는 메가 폼 (예: 한카리아스, 메가한카리아스, 메가한카리아스Z)."""
    base = row["en"].lower().replace(" ", "-").replace(".", "").replace("'", "")
    out = [{"label": row.get("ko") or row["en"], "value": row["en"]}]
    for slug in sorted(_load().get("_slugs") or []):
        if slug.startswith(base + "-mega"):
            tail = slug[len(base) + 5:].replace("-", "").upper()
            en = row["en"] + "-Mega" + (f"-{tail}" if tail else "")
            out.append({"label": f"메가{row.get('ko') or row['en']}{tail}", "value": en})
    return out


FORM_KO = {v: k for k, v in FORM_PREFIXES.items()}


@safe_tool
def resolve_names(names: list[str], kind: Literal["pokemon", "move"] = "pokemon") -> dict:
    if not names or any(not str(n).strip() for n in names):
        raise ToolError(ErrorCode.BAD_ARGUMENT, "names에 빈 이름이 있습니다.")
    results = [resolve_one(n, kind) for n in names]
    if not any(r["found"] for r in results):
        if any(r.get("reason") in ("AMBIGUOUS", "SUGGEST") for r in results):
            raise ToolError(ErrorCode.AMBIGUOUS, "이름이 여러 대상에 해당합니다. 정확한 이름을 다시 물어보세요.")
        raise ToolError(ErrorCode.NOT_FOUND, f"이름을 찾지 못했습니다: {', '.join(names)}")
    return ok({"kind": kind, "results": results, "source": "로컬 매핑 (PokeAPI 수집본)"})


# ---------------------------------------------------------------- 화면용 검색 (팀 직접 선택)
SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/{id}.png"


def sprite_url(pokeapi_name: str | None) -> str | None:
    pid = _load()["_ids"].get(pokeapi_name or "")
    return SPRITE_URL.format(id=pid) if pid else None


def _card(r: dict) -> dict:
    return {"label": r.get("ko") or r["en"], "value": r["en"], "sprite": sprite_url(r.get("pokeapi_name"))}


def search(q: str, limit: int = 12, allow_mega: bool = True) -> list[dict]:
    """한국어/영문 일부로 포켓몬 후보를 찾는다: 정확히 일치 → 앞부분 일치 → 포함 → 비슷한 이름 순."""
    q = q.strip()
    if not q:
        return []
    out: list[dict] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        r = resolve_one(name, "pokemon")
        if not r.get("found") or r["en"] in seen:
            return
        if not allow_mega and r.get("is_mega"):
            return
        seen.add(r["en"])
        out.append(_card(r))
        if allow_mega and not r.get("is_mega"):  # 그 종의 실제 메가 폼(X/Y/Z 포함)도 바로 고를 수 있게
            for v in _species_variants({"en": r["en"], "ko": r.get("ko")})[1:]:
                add(v["value"])

    compact = q.replace(" ", "")
    if allow_mega and compact.startswith("메가") and len(compact) > 2:
        # '메가한' → 한…으로 시작하는 종의 메가 폼들 (메가한카리아스, 메가한카리아스Z …)
        for c in search(compact[2:], limit * 2, True):
            if "-Mega" in c["value"] and c["value"] not in seen:
                seen.add(c["value"])
                out.append(c)
    add(q)
    keys = [k for k in _load()["pokemon"] if k and not k.isascii()] if not compact.isascii() else \
           [k for k in _load()["pokemon"] if k and k.isascii()]
    key_q = compact if not compact.isascii() else to_id(compact)
    for pred in (lambda k: k.startswith(key_q), lambda k: key_q in k):
        for k in sorted(keys, key=len):
            if len(out) >= limit:
                return out
            if pred(k):
                add(k)
    for c in suggest(q, "pokemon"):
        if len(out) >= limit:
            break
        add(c["value"])
    return out[:limit]
