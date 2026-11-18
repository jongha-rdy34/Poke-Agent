"""기획 검증 '도구 연결 확인': LLM API를 한 번 호출하고, Tool 명세를 넘겼을 때 Tool을 고르는지 확인한다.

실행: python scripts/check_llm.py   (backend 폴더, .env에 GMS_API_KEY / GMS_BASE_URL 설정 후)
키 값은 출력하지 않는다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402
from app.tools import list_specs  # noqa: E402

if not config.GMS_API_KEY:
    print("[INVALID_KEY] GMS_API_KEY가 설정되지 않았습니다. backend/.env를 확인하세요 (.env.example 참고).")
    sys.exit(1)

try:
    from openai import OpenAI
except ImportError:
    print("openai 패키지가 없습니다: pip install -r requirements.txt")
    sys.exit(1)

client = OpenAI(api_key=config.GMS_API_KEY, base_url=config.GMS_BASE_URL or None)

# 1) 단순 호출
r = client.chat.completions.create(
    model=config.LLM_MODEL,
    messages=[{"role": "user", "content": "한 단어로 대답해: 포켓몬 대전에서 상대보다 먼저 행동하는 것을 뭐라고 해?"}],
)
print("[1] LLM 응답:", r.choices[0].message.content)

# 2) Tool 선택 확인
tools = [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                           "parameters": s["input_schema"]}} for s in list_specs()]
for q in ["상대 한카리아스가 주로 무슨 기술을 들고 나와?", "따라큐 스피드 종족값 알려줘", "안녕!"]:
    r = client.chat.completions.create(model=config.LLM_MODEL, tools=tools,
                                       messages=[{"role": "user", "content": q}])
    calls = r.choices[0].message.tool_calls or []
    picked = [(c.function.name, json.loads(c.function.arguments or "{}")) for c in calls]
    print(f"[2] {q!r} -> {picked or '(Tool 호출 없음)'}")
