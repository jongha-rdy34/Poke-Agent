"""테스트용 가짜 LLM: 분석 결과를 미리 정해 두고, 응답 문장 생성은 실패시켜 템플릿 경로도 확인한다."""
from __future__ import annotations

from app.agent.analyze import Analysis


class _Msg:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    def __init__(self, analyses: list[dict], answer: str | None = None):
        self.analyses = list(analyses)
        self.answer = answer
        self.calls = 0

    def with_structured_output(self, schema, **_):
        parent = self

        class _S:
            def invoke(self, messages):
                parent.calls += 1
                return Analysis(**parent.analyses.pop(0))
        return _S()

    def invoke(self, messages):
        self.calls += 1
        if self.answer is None:
            raise RuntimeError("fake llm: no answer configured")
        return _Msg(self.answer)
