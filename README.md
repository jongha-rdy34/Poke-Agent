# Poke-Agent — 포켓몬 대전 선출·기술 플래너

> AI 코어 프로젝트 3회차 · Agent 기반 도메인 플래너 서비스

**"내 포켓몬 6마리와 상대 포켓몬 6마리를 보고, 어떤 3마리를 선출하고 어떤 기술을 쓸지"** 를
실제 메타 통계·종족값·타입 상성으로 **확인한 값만으로** 계산해 제안하는 Agent입니다.
LLM은 요청 분석과 문장화만 맡고, 배율·스피드·조합 계산은 코드가 합니다.

## 진행 현황

| 일차 | 범위 | 상태 |
|---|---|---|
| 1일차 | 기획(2.1~2.7), Tool F3001~F3004, FastAPI 골격 | ✅ |
| 2일차 | 메인 Agent(F3005~F3011), 로깅(F3012~F3014), 계획·서버·화면(F3015~F3019) | ⏳ |
| 3일차 | 심화(F3020~F3023), 검증, 제출 | ⏳ |

## 문서

| 문서 | 내용 |
|---|---|
| [요구사항 정의서](docs/01_요구사항_정의서.md) | 도메인 확정 근거, 시나리오, 요구사항 표, 외부 API 출처·이용 조건 |
| [Tool 정의서](docs/02_Tool_정의서.md) | Tool별 이름·설명·입출력·실패 코드, 등록/확장 방법 |
| [Agent 설계](docs/03_Agent_설계.md) | 실행 흐름, 판단 계산식, 재계획, 메모리, 이벤트, 저장 구조, API |
| [기획 검증](docs/04_기획_검증.md) | API 호출 확인, 실제 응답 대조, 설계 시험과 설계 변경 |

## 실행 방법

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # 값 채우기 (아래 참고)
```

```bash
python run_tools.py          # Tool 단독 실행 (정상 1회 + 실패 2가지)
python -m pytest -q          # 오프라인 단위 테스트
uvicorn app.main:app --reload   # http://localhost:8000/docs
```

## API 키 / 환경변수

| 변수 | 필요 | 설명 |
|---|---|---|
| `GMS_API_KEY` | 2일차부터 | SSAFY GMS 키 (LLM). `python scripts/check_llm.py`로 연결 확인 |
| `GMS_BASE_URL`, `LLM_MODEL` | 2일차부터 | GMS 접속 주소, 모델 |
| `DEFAULT_FORMAT` | 선택 | 기본 `gen9championsbssregmc` (싱글 6→3 선출) |

외부 데이터 API 세 곳은 **키가 필요 없습니다.** 키는 `.env`에만 두고 코드·커밋에 넣지 않습니다.

## 외부 API 출처와 이용 조건

| API | 용도 | 이용 조건 | 준수 방법 |
|---|---|---|---|
| [Smogon Usage Stats](https://www.smogon.com/stats/) | 상대 예상 세트·사용률 (F3002) | 공개 월간 통계, 약관/한도 명시 없음 | 출처 표기, 월별 파일 30일 캐시 |
| [PokeAPI](https://pokeapi.co/) | 타입·종족값·특성·한국어 이름 (F3003) | 무료, 공정 이용 정책(로컬 캐시 권장, 남용 시 차단) | 7일 캐시, 한국어 이름 1회 수집 후 재사용 |
| [Pokémon Showdown data](https://play.pokemonshowdown.com/data/) | 기술 정보·타입 상성 (F3004) | 공개 데이터, 소스 MIT 라이선스 | 24시간 캐시, 출처 표기 |

포켓몬 관련 이름·데이터의 권리는 Nintendo / Creatures / GAME FREAK에 있으며, 이 프로젝트는 비상업 교육용입니다.
추천은 **통계 기반 추정**이며, 상대의 실제 세트와 다를 수 있습니다.

## 구조

```
backend/
  app/
    main.py              FastAPI (1일차: /health, /api/tools)
    config.py            환경변수
    tools/
      common.py          ok/fail 응답, 실패 코드, HTTP+캐시, safe_tool
      registry.py        F3001 Tool 명세·등록, call_tool
      smogon.py          F3002 get_meta_usage
      pokeapi.py         F3003 get_pokemon_detail
      showdown.py        F3004 get_move_info, get_type_matchup
      names.py           (로컬) resolve_names — 한국어 → 영문 식별자
    data/name_map.json   한국어 이름 매핑 (PokeAPI 수집본)
  scripts/
    build_name_map.py    한국어 이름 매핑 재생성
    design_trial.py      기획 검증 설계 시험
    check_llm.py         LLM 연결·Tool 선택 확인
  tests/test_tools.py
  run_tools.py           Tool 단독 실행
docs/                    기획·설계·검증 문서
frontend/                (2일차)
```

## Tool 요약

| Tool | 구분 | 언제 쓰나 |
|---|---|---|
| `get_meta_usage` | 외부 | 상대가 실전에서 쓰는 기술·도구·배분 비율, 메타 상위 후보 |
| `get_pokemon_detail` | 외부 | 타입·종족값(스피드)·특성 |
| `get_move_info` | 외부 | 기술 타입·분류·위력·우선도 |
| `get_type_matchup` | 외부 | 타입 상성 배율 (면역 특성 포함) |
| `resolve_names` | 로컬 | 한국어 이름 → 다른 Tool 입력용 식별자 |

모든 Tool은 `{"ok": true, "data": …}` 또는 `{"ok": false, "error": {"code", "message"}}`로만 응답합니다.
