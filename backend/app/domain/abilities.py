"""데미지에 영향을 주는 특성 (Pokémon Showdown data/abilities.ts의 배율과 같은 값).

- 타입 무효·반감 특성(부유, 저수, 타오르는불꽃, 두꺼운지방, 내열 등)은 타입 상성표에서 이미 반영한다 (get_type_matchup).
- 여기서는 그 외에 '데미지 숫자'를 바꾸는 특성을 다룬다. 날씨·필드·체력 조건부 특성은 가정을 밝히고 반영한다.
"""
from __future__ import annotations

# -스킨: 노말 기술의 타입을 바꾸고 위력 1.2배
ATE = {"pixilate": "Fairy", "aerilate": "Flying", "refrigerate": "Ice", "galvanize": "Electric", "dragonize": "Dragon"}

KO = {
    "technician": "테크니션", "toughclaws": "단단한발톱", "ironfist": "철주먹", "strongjaw": "옹골찬턱",
    "megalauncher": "메가런처", "sharpness": "예리함", "punkrock": "펑크록", "reckless": "이판사판",
    "hugepower": "천하장사", "purepower": "순수한힘", "gorillatactics": "무아지경", "hustle": "의욕",
    "waterbubble": "수포", "steelworker": "강철술사", "steelyspirit": "강철정신", "transistor": "트랜지스터",
    "dragonsmaw": "용의턱", "rockypayload": "바위나르기", "adaptability": "적응력",
    "pixilate": "페어리스킨", "aerilate": "스카이스킨", "refrigerate": "프리즈스킨", "galvanize": "일렉트릭스킨",
    "dragonize": "드래곤스킨",
    "multiscale": "멀티스케일", "shadowshield": "스펙터가드", "icescales": "얼음인분", "furcoat": "털가죽",
    "fluffy": "복슬복슬", "filter": "필터", "solidrock": "하드록", "prismarmor": "프리즘아머",
}


def attacker_mods(ability: str | None, move: dict, move_type: str, physical: bool) -> tuple[list[float], list[float], float, list[str]]:
    """(위력 배율들, 공격 배율들, 자속 배율, 적용된 특성 설명)."""
    a = ability or ""
    bp, atk, notes = [], [], []
    flags = set(move.get("flags") or [])
    power = move.get("base_power") or 0
    if a == "technician" and power <= 60:
        bp.append(1.5)
    if a == "toughclaws" and "contact" in flags:
        bp.append(1.3)
    if a == "ironfist" and "punch" in flags:
        bp.append(1.2)
    if a == "strongjaw" and "bite" in flags:
        bp.append(1.5)
    if a == "megalauncher" and "pulse" in flags:
        bp.append(1.5)
    if a == "sharpness" and "slicing" in flags:
        bp.append(1.5)
    if a == "punkrock" and "sound" in flags:
        bp.append(1.3)
    if a == "reckless" and move.get("recoil"):
        bp.append(1.2)
    if a in ATE and move.get("type") == "Normal":
        bp.append(1.2)
    if a in ("hugepower", "purepower") and physical:
        atk.append(2.0)
    if a in ("gorillatactics", "hustle") and physical:
        atk.append(1.5)
    if a == "waterbubble" and move_type == "Water":
        atk.append(2.0)
    if a in ("steelworker", "steelyspirit") and move_type == "Steel":
        atk.append(1.5)
    if a == "transistor" and move_type == "Electric":
        atk.append(1.3)
    if a == "dragonsmaw" and move_type == "Dragon":
        atk.append(1.5)
    if a == "rockypayload" and move_type == "Rock":
        atk.append(1.5)
    stab = 2.0 if a == "adaptability" else 1.5
    if bp or atk or a == "adaptability" or (a in ATE and move.get("type") == "Normal"):
        notes.append(f"공격측 특성 {KO.get(a, a)}")
    return bp, atk, stab, notes


def defender_mods(ability: str | None, move: dict, move_type: str, physical: bool, effectiveness: float,
                  full_hp: bool = True) -> tuple[list[float], list[float], list[str]]:
    """(방어 배율들, 최종 데미지 배율들, 적용된 특성 설명). 멀티스케일은 체력이 가득 찼다고 가정."""
    a = ability or ""
    df, final, notes = [], [], []
    flags = set(move.get("flags") or [])
    if a == "furcoat" and physical:
        df.append(2.0)
    if a in ("multiscale", "shadowshield") and full_hp:
        final.append(0.5)
    if a == "icescales" and not physical:
        final.append(0.5)
    if a == "fluffy":
        if "contact" in flags:
            final.append(0.5)
        if move_type == "Fire":
            final.append(2.0)
    if a in ("filter", "solidrock", "prismarmor") and effectiveness > 1:
        final.append(0.75)
    if a == "punkrock" and "sound" in flags:
        final.append(0.5)
    if df or final:
        notes.append(f"방어측 특성 {KO.get(a, a)}" + (" (체력 가득 가정)" if a in ("multiscale", "shadowshield") else ""))
    return df, final, notes


def effective_type(ability: str | None, move: dict) -> str:
    return ATE[ability] if ability in ATE and move.get("type") == "Normal" else move["type"]
