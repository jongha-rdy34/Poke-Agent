"""PokeAPI에서 포켓몬/기술의 한국어 이름을 한 번 수집해 로컬 매핑 파일로 저장한다.

- 실행: python scripts/build_name_map.py               (backend 폴더에서, 전체 수집)
        python scripts/build_name_map.py --forms-only  (기존 파일에 폼 이름만 추가)
- 결과: app/data/name_map.json
- PokeAPI 공정 이용 정책(결과를 로컬에 캐시)에 따라 1회 수집 후 재사용한다.
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BASE = "https://pokeapi.co/api/v2"
OUT = Path(__file__).resolve().parents[1] / "app" / "data" / "name_map.json"
session = requests.Session()


def to_id(text: str) -> str:
    """Showdown/Smogon 방식 ID: 영문 소문자+숫자만 남긴다."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def fetch(url: str) -> dict | None:
    for _ in range(3):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
    return None


def names_of(data: dict) -> tuple[str | None, str | None]:
    ko = en = None
    for n in data.get("names", []):
        lang = n["language"]["name"]
        if lang == "ko":
            ko = n["name"]
        elif lang == "en":
            en = n["name"]
    return ko, en


def collect(kind: str) -> list[dict]:
    listing = fetch(f"{BASE}/{kind}?limit=3000")
    urls = [r["url"] for r in listing["results"]]
    out: list[dict] = []

    def one(url: str) -> dict | None:
        d = fetch(url)
        if not d:
            return None
        ko, en = names_of(d)
        if not en:
            return None
        return {"slug": d["name"], "en": en, "ko": ko, "id": to_id(en)}

    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, item in enumerate(ex.map(one, urls), 1):
            if item:
                out.append(item)
            if i % 200 == 0:
                print(f"  {kind}: {i}/{len(urls)}", file=sys.stderr)
    return out


def slug_to_showdown(slug: str) -> str:
    """'rotom-wash' -> 'Rotom-Wash' (Showdown 표기, ID는 to_id로 'rotomwash')"""
    return "-".join(part.capitalize() for part in slug.split("-"))


def collect_forms() -> list[dict]:
    """폼 이름 수집: 워시로토무(rotom-wash)처럼 한국어 폼 이름이 따로 있는 경우.

    PokeAPI pokemon-form의 names(전체 이름) 또는 form_names(폼 이름)에서 한국어를 읽는다.
    """
    listing = fetch(f"{BASE}/pokemon-form?limit=3000")
    urls = [r["url"] for r in listing["results"]]

    def one(url: str) -> dict | None:
        d = fetch(url)
        if not d or not d.get("form_name"):  # 기본 폼(폼 이름 없음)은 종 이름으로 충분
            return None
        ko_full = next((n["name"] for n in d.get("names", []) if n["language"]["name"] == "ko"), None)
        ko_form = next((n["name"] for n in d.get("form_names", []) if n["language"]["name"] == "ko"), None)
        slug = d["pokemon"]["name"]
        en = slug_to_showdown(slug)
        return {"slug": slug, "en": en, "id": to_id(en), "ko": ko_full, "ko_form": ko_form,
                "species_slug": slug.split("-")[0]}

    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, item in enumerate(ex.map(one, urls), 1):
            if item and (item["ko"] or item["ko_form"]):
                out.append(item)
            if i % 300 == 0:
                print(f"  pokemon-form: {i}/{len(urls)}", file=sys.stderr)
    return out


def main() -> None:
    if "--forms-only" in sys.argv and OUT.exists():
        data = json.loads(OUT.read_text(encoding="utf-8"))
    else:
        data = {"source": "PokeAPI (pokeapi.co)", "species": collect("pokemon-species"), "moves": collect("move")}
    data["forms"] = collect_forms()
    # 존재하는 포켓몬(폼 포함) 전체 목록: '메가고릴타'처럼 없는 폼을 지어내지 않도록 확인용
    listing = fetch(f"{BASE}/pokemon?limit=3000")["results"]
    data["pokemon_ids"] = {r["name"]: int(r["url"].rstrip("/").split("/")[-1]) for r in listing}  # 이미지 주소용
    data["pokemon_slugs"] = sorted(data["pokemon_ids"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"saved {len(data['species'])} species, {len(data['forms'])} forms, {len(data['moves'])} moves, "
          f"{len(data['pokemon_slugs'])} pokemon -> {OUT}")


if __name__ == "__main__":
    main()
