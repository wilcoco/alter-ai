"""기저 어댑터의 요청 모양과 응답 파싱.

실 API 키 없이도 이 경로가 깨지면 잡히게 한다 — 배포 환경에서 키를 넣는 순간
처음 도는 코드이므로, 여기서 안 잡으면 프로덕션에서 잡힌다.
"""

from __future__ import annotations

import json
import types

import pytest

from app.capture.damage import make_advisor
from app.capture.llm import AnthropicLLM, StubLLM
from app.capture.pic import capture
from app.core.ontology import Node, NodeStatus, NodeType


class _Block:
    def __init__(self, text: str, type: str = "text") -> None:
        self.text = text
        self.type = type


class _FakeMessages:
    """호출 인자를 기록하고 미리 정한 텍스트를 돌려준다."""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            content=[_Block("무시되는 사고 블록", "thinking"), _Block(self.payload)]
        )


def _llm(payload: str) -> tuple[AnthropicLLM, _FakeMessages]:
    llm = AnthropicLLM.__new__(AnthropicLLM)   # __init__ 의 SDK 생성을 우회
    fake = _FakeMessages(payload)
    llm._client = types.SimpleNamespace(messages=fake)
    llm._model = "claude-opus-5"
    llm._max_tokens = 8000
    llm.name = "claude-opus-5"
    return llm, fake


def test_answer_sends_adaptive_thinking_and_reads_only_text_blocks():
    llm, fake = _llm("답변 본문")
    out = llm.answer("질문", context="")
    assert out == "답변 본문"                     # thinking 블록은 섞이지 않는다
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["messages"][0]["role"] == "user"


def test_injected_context_reaches_the_prompt_as_plain_text():
    """산출물 층위 접합 — 정본은 오직 텍스트로만 기저에 들어간다."""
    llm, fake = _llm("답")
    llm.answer("질문", context="- [conclusion] 점도를 낮추면 불량이 준다")
    content = fake.calls[0]["messages"][0]["content"]
    assert "점도를 낮추면 불량이 준다" in content
    assert "검증된 정본 지식" in content


def test_extract_enforces_a_json_schema_and_parses_the_result():
    llm, fake = _llm(json.dumps({"conflicts": []}))
    result = llm.extract("프롬프트", {"type": "object"})
    assert result == {"conflicts": []}
    assert fake.calls[0]["output_config"]["format"]["type"] == "json_schema"


def test_pic_capture_builds_the_inference_chain():
    payload = json.dumps(
        {
            "summary": "요약",
            "premises": [{"title": "전제1", "content": "p"}],
            "inferences": [{"title": "추론1", "content": "i"}],
            "conclusions": [{"title": "결론1", "content": "c"}],
            "concepts": ["점도"],
            "unresolved": ["손끝 감각"],
        }
    )
    llm, _fake = _llm(payload)
    graph = capture(llm, "질문", "답")

    types_seen = {n.type for n in graph.nodes}
    assert {NodeType.PREMISE, NodeType.INFERENCE, NodeType.CONCLUSION} <= types_seen
    assert graph.edges, "P→I→C 사슬이 안 만들어졌다 — 배당 라우팅이 끊긴다"
    # 명제로 안 담긴 것은 삭제하지 않고 기록으로 남는다 (암묵지 병목)
    assert "손끝 감각" in graph.summary


def test_pic_capture_falls_back_instead_of_losing_the_answer():
    """구조화가 실패해도 문답은 증발하지 않는다."""
    graph = capture(StubLLM(), "질문", "기저의 답 본문. 두 번째 문장.")
    assert graph.fallback
    assert graph.nodes and graph.nodes[0].content.startswith("기저의 답")


def test_advisor_drops_hallucinated_and_low_confidence_items():
    payload = json.dumps(
        {
            "conflicts": [
                {"canonical_id": "real", "detail": "진짜 모순", "confidence": 0.9},
                {"canonical_id": "real", "detail": "애매함", "confidence": 0.2},
                {"canonical_id": "존재하지않음", "detail": "환각", "confidence": 1.0},
            ]
        }
    )
    llm, _fake = _llm(payload)
    advise = make_advisor(llm)

    candidate = Node(id="cand", type=NodeType.CLAIM, title="후보")
    canonical = [
        Node(id="real", type=NodeType.CLAIM, title="정본", status=NodeStatus.CANONICAL)
    ]
    advisories = advise(candidate, canonical)

    assert len(advisories) == 1                   # 저확신·환각은 버려진다
    assert advisories[0].canonical_id == "real"
    assert advisories[0].confidence == 0.9
    # 제보는 손상 신호 타입이 아니다 — 관문에 넣으려면 새 코드를 써야 한다
    from app.core.promotion import Advisory, Conflict
    assert isinstance(advisories[0], Advisory)
    assert not isinstance(advisories[0], Conflict)


def test_stub_llm_declares_itself_instead_of_inventing_an_answer():
    out = StubLLM().answer("질문", "")
    assert "stub" in out.lower()
    assert StubLLM().extract("아무거나", {}) == {}
