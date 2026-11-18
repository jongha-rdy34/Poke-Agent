"""PokeAPI에서 포켓몬/기술의 한국어 이름을 한 번 수집해 로컬 매핑 파일로 저장한다.

- 실행: python scripts/build_name_map.py   (backend 폴더에서)
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


def main() -> None:
    species = collect("pokemon-species")
    moves = collect("move")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"source": "PokeAPI (pokeapi.co)", "species": species, "moves": moves},
                   ensure_ascii=False, indent=0),
        encoding="utf-8",
    )
    print(f"saved {len(species)} species, {len(moves)} moves -> {OUT}")


if __name__ == "__main__":
    main()
