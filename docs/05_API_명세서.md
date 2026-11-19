# API 명세서 (F3017)

- 기본 주소: `http://localhost:8000` · Swagger: `/docs` · 화면: `/app/`
- 요청·응답 본문은 JSON, 형식은 Pydantic 모델(`backend/app/api/schemas.py`)로 정의

## 1. 대화

### `POST /api/chat`
메인 Agent 실행. 같은 대화를 이으려면 이전 응답의 `session_id`를 함께 보낸다.

요청
```json
{ "message": "내 팀: 하마돈, 마스카나, … / 상대 팀: 누리레느, 한카리아스, … 3마리 뭐 뽑아?", "session_id": null }
```
응답 `200`
```json
{
  "session_id": "s-173b20576f", "run_id": "r-…",
  "status": "ok",                 // ok | ask(되묻기) | limit(재계획 상한) | error
  "intent": "plan_selection", "intent_reason": "내 팀과 상대 팀을 주고 선출을 물음",
  "answer": "**선출 3마리**: …",
  "plan_draft": { "format": "…", "my_team": [], "opponent_team": [], "selected": [], "lead": "…",
                  "move_plan": [{"opponent","my_pokemon","move","offense","speed","answered"}], "basis": {} },
  "saved": null,                  // 대화로 저장했을 때 {plan, overlaps}
  "replans": [{"round": 1, "reason": "잡지 못한 상대: …", "criteria": "…"}],
  "error": null,                  // {code, message} — INVALID_KEY면 키 설정 안내
  "events": [ /* 실행 이벤트 (실행 순서) — 아래 4절 */ ],
  "total_ms": 1222
}
```
Tool 실패·LLM 오류도 `200`으로 오고 `status`/`error`로 구분한다 (서비스가 멈추지 않음).

## 2. 계획 관리

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | `/api/plans?date=YYYY-MM-DD` | — | `Plan[]` (날짜 내림차순) |
| POST | `/api/plans` | `PlanCreate` | `201 {plan, overlaps[]}` |
| GET | `/api/plans/{id}` | — | `Plan` / `404` |
| PUT | `/api/plans/{id}` | `PlanUpdate` (바꿀 필드만) | `{plan, overlaps[]}` / `404` / `422` |
| DELETE | `/api/plans/{id}` | — | `204` / `404` |

`PlanCreate`
```json
{ "title": "토요일 대회 1R", "battle_date": "2026-11-21", "start_time": "15:00", "end_time": "15:30",
  "format": "gen9championsbssregmc", "my_team": ["…"], "opponent_team": ["…"],
  "selected": ["마스카나", "보만다", "루카리오"], "lead": "마스카나",
  "move_plan": [{"opponent": "한카리아스", "my_pokemon": "마스카나", "move": "트리플악셀", "offense": 4, "speed": "선공 유력"}],
  "basis": {"month": "2026-10", "rating": 1500}, "memo": null, "session_id": "s-…" }
```
- `plan_draft`에 일정(title, battle_date, start_time, end_time)만 더하면 그대로 저장 가능
- 검증: 날짜 `YYYY-MM-DD`, 시각 `HH:MM`, `end_time > start_time` → 위반 시 `422`
- **겹침**: 같은 `battle_date`에서 `start < 다른.end && end > 다른.start`인 계획이 있으면 **저장은 하고** `overlaps`로 알린다
- `Plan`에는 `id`, `created_at`, `updated_at`이 더해진다

## 3. 실행 로그 (수정 API 없음)

| 메서드 | 경로 | 응답 |
|---|---|---|
| GET | `/api/logs` | `[{session_id, runs, events, first_ts, last_ts, tool_calls, replans}]` |
| GET | `/api/logs/{session_id}` | `[{id, session_id, run_id, seq, ts, type, step, payload}]` 실행 순서 / `404` |
| DELETE | `/api/logs/{session_id}` | `204` / `404` |

`PUT`, `PATCH`는 `405`. 기록을 고칠 수 있으면 관찰의 의미가 사라지기 때문이다.

## 4. 실행 이벤트 형식
```json
{"session_id": "s-…", "run_id": "r-…", "seq": 12, "ts": "2026-11-19T03:31:12.870+00:00",
 "type": "tool_call_end", "step": "execute",
 "payload": {"tool": "get_meta_usage", "args": {}, "ok": true, "error_code": null, "duration_ms": 3}}
```
| type | payload |
|---|---|
| `run_start` / `run_end` | message / status, total_ms, tool_calls, replans |
| `step_start` / `step_end` | round / duration_ms, status, summary |
| `llm_decision` | decision=intent, intent, reason |
| `decision` | 코드 판단: ask(missing/unresolved), no_replan, stop |
| `plan` | round, steps, tools |
| `tool_call_start` / `tool_call_end` | tool, args / ok, error_code, error_message, duration_ms |
| `replan` | round, reason, criteria |
| `plan_saved` | plan_id, overlaps |
| `error` | code, message, (fallback) |

## 5. 시스템 · Tool
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | `{status, tools, llm_key(설정 여부만), logging_agent}` |
| GET | `/api/tools` | Tool 명세 목록 |
| POST | `/api/tools/{name}` | Tool 단독 실행 (개발·검증용) |
