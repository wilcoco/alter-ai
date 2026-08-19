"""기저 LLM 어댑터 — **산출물 층위 접합만** (spec §3).

설계 원칙 (CLAUDE.md "위반 금지"):

    기저 LLM 은 산출물 층위로만 접합한다. 텍스트 in / 텍스트 out. 내부 표현
    (은닉 상태·로짓)에는 절대 개입하지 않는다. 기저 교체 가능성이 전략 자산이다.

그래서 이 파일이 얇다. 여기가 두꺼워지면 기저에 종속된 것이고, 그건 전략을
잃은 것이다. 이 모듈의 인터페이스는 두 개뿐이다 — ``answer`` 와 ``extract``.
기저를 GPT/오픈웨이트로 갈아끼우려면 :class:`BaseLLM` 구현체 하나만 더 쓰면 된다.

``ANTHROPIC_API_KEY`` 가 없으면 :class:`StubLLM` 로 떨어진다. 한 바퀴의 구조
(포획 → 검증 → 승격 → 주입)는 기저가 stub 여도 그대로 돈다 — 기저 독립성의
실측이기도 하다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Answer:
    """기저의 답 + 라우팅 흔적.

    ``injected_ids`` 는 이 답을 만들 때 주입된 정본 노드들이다. 승격 (a)경로가
    실제로 다음 문답에 영향을 줬다는 증거이자, 정산 역류(spec §8)의 입구다.
    """

    text: str
    model: str
    injected_ids: list[str]
    stubbed: bool = False


class BaseLLM(Protocol):
    """교체 가능 부품의 계약. 텍스트 in / 텍스트 out, 그게 전부."""

    name: str

    def answer(self, question: str, context: str) -> str: ...

    def extract(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class StubLLM:
    """키 없이 뜨는 모드. 답을 지어내는 대신 **답이 없다고 말한다.**

    그럴듯한 가짜 답을 만들면 폴립층이 쓰레기로 차고, 검증 프로토콜의
    "합의인가 보간인가" 구분이 무너진다. stub 는 자기가 stub 임을 밝힌다.
    """

    name = "stub"

    def answer(self, question: str, context: str) -> str:
        head = (
            "⚠ 기저 LLM 미연결 (stub 모드). ANTHROPIC_API_KEY 를 설정하면 실제 "
            "기저 답변이 들어온다.\n\n"
            f"받은 질문: {question}\n"
        )
        if context.strip():
            head += (
                "\n주입된 정본 지식이 있다 — 승격 (a)경로는 stub 모드에서도 "
                f"동작한다:\n{context}"
            )
        else:
            head += "\n주입할 정본 지식 없음 (온톨로지가 비었거나 관련 노드 없음)."
        return head

    def extract(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {}


class AnthropicLLM:
    """Anthropic Messages API 접합. 호출은 텍스트 경계에서만 일어난다."""

    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self.name = model

    def answer(self, question: str, context: str) -> str:
        system = (
            "너는 coral 시스템의 기저 층이다. 사용자 질문에 정확하고 간결하게 "
            "답하되, 다음 두 가지를 반드시 지켜라.\n"
            "1) 주장의 지위를 구분해서 표기한다 — [문헌 합의] / [내 보간] / "
            "[불확실]. 확신도를 높/중/저로 밝힌다.\n"
            "2) 아래 '검증된 정본 지식'이 주어지면 그것을 우선 근거로 삼고, "
            "정본과 어긋나는 답을 낼 때는 어긋난다는 사실을 명시한다.\n"
            "추론이 있는 답은 전제 → 추론 → 결론이 드러나게 쓴다."
        )
        content = question
        if context.strip():
            content = (
                f"[검증된 정본 지식 — 이 시스템이 승격시킨 것]\n{context}\n\n"
                f"[질문]\n{question}"
            )
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": content}],
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def extract(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """스키마 강제 구조화 추출 (P→I→C 포획·손상 시험이 쓴다)."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        if not text:
            return {}
        return json.loads(text)


_cached: BaseLLM | None = None


def get_llm() -> BaseLLM:
    """프로세스당 한 번 만들어 재사용."""
    global _cached
    if _cached is not None:
        return _cached
    if settings.llm_enabled:
        try:
            _cached = AnthropicLLM(
                api_key=settings.anthropic_api_key or "",
                model=settings.base_model,
                max_tokens=settings.max_tokens,
            )
            log.info("기저 LLM: %s", settings.base_model)
        except Exception as exc:  # SDK 미설치·키 불량 등 — 서비스는 계속 뜬다
            log.warning("기저 LLM 초기화 실패, stub 로 대체: %s", exc)
            _cached = StubLLM()
    else:
        log.info("ANTHROPIC_API_KEY 없음 — stub 기저로 기동")
        _cached = StubLLM()
    return _cached


def reset_llm_cache() -> None:
    """테스트용."""
    global _cached
    _cached = None
