"""F3012 실행 이벤트 발행·구독.

- 메인 Agent는 Emitter로 이벤트를 '외치기만' 하고, 누가 듣는지 모른다.
- 구독자(로깅 Agent 등)에서 예외가 나도 버스가 삼키므로 메인 흐름에 영향이 없다.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime
from typing import Callable

Subscriber = Callable[[dict], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            if fn not in self._subs:
                self._subs.append(fn)

    def unsubscribe(self, fn: Subscriber) -> None:
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def publish(self, event: dict) -> None:
        for fn in list(self._subs):
            try:
                fn(event)
            except Exception:  # noqa: BLE001 - 구독자 실패는 발행 측에 전파하지 않는다
                pass


bus = EventBus()


class Emitter:
    """한 번의 실행(run)에서 이벤트를 만들어 버스에 발행한다. 화면 표시용으로 자기 기록도 남긴다."""

    def __init__(self, session_id: str, event_bus: EventBus = bus) -> None:
        self.session_id = session_id
        self.run_id = "r-" + uuid.uuid4().hex[:10]
        self.bus = event_bus
        self.events: list[dict] = []
        self._seq = 0
        self._lock = threading.Lock()
        self.started = time.perf_counter()

    def emit(self, type_: str, step: str | None = None, **payload) -> dict:
        with self._lock:
            self._seq += 1
            event = {
                "session_id": self.session_id,
                "run_id": self.run_id,
                "seq": self._seq,
                "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "type": type_,
                "step": step,
                "payload": payload,
            }
            self.events.append(event)
            # 병렬 Tool 호출에서도 구독자가 seq 순서대로 받도록 발행까지 잠금 안에서 한다
            self.bus.publish(event)
        return event
