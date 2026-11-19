"""F3013 로깅 서브 Agent: 이벤트를 구독해 세션 단위 실행 로그로 저장한다.

수집과 저장에는 LLM을 쓰지 않는다. 받은 이벤트를 발생 순서대로 그대로 덧붙인다.
저장에 실패해도 대화는 정상 동작해야 하므로 실패는 카운트만 남긴다.
"""
from __future__ import annotations

from app.events.bus import EventBus, bus
from app.storage import logs


class LoggingAgent:
    def __init__(self, event_bus: EventBus = bus) -> None:
        self.bus = event_bus
        self.failures = 0
        self.saved = 0
        self.attached = False

    def on_event(self, event: dict) -> None:
        try:
            logs.append(event)
            self.saved += 1
        except Exception:  # noqa: BLE001
            self.failures += 1

    def attach(self) -> None:
        self.bus.subscribe(self.on_event)
        self.attached = True

    def detach(self) -> None:
        self.bus.unsubscribe(self.on_event)
        self.attached = False


logging_agent = LoggingAgent()
