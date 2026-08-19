"""P→I→C 포획 — 대화를 추론 구조로 굳힌다.

H2A2H2 의 ADR-0001: **"지식을 텍스트가 아니라 규칙/구조로 포획한다."**
평평한 텍스트는 관찰(Pearl 1층) 데이터이고, 전제→추론→결론 구조는 추론 자체의
데이터다 (spec §4 — 상부망 훈련 신호 가설의 근거).

한계 (spec §4, 정직하게 남긴다): **절차적·감각적 지식은 명제 구조에 담기지
않는다.** 암묵지 병목은 포획 단계에서 이미 재등장하며, 이 모듈은 그것을 풀지
않는다. 여기서 나오는 그래프는 "명제화 가능한 부분"에 한정된다.

출력 스키마는 H2A2H2 ``src/types/graph.ts`` 와 포맷 호환이다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.capture.llm import BaseLLM
from app.core.ontology import EdgeType, NodeType

log = logging.getLogger(__name__)


@dataclass
class DraftNode:
    type: NodeType
    title: str
    content: str = ""
    #: 임시 로컬 id — 저장 시 실제 노드 id 로 치환된다.
    ref: str = ""


@dataclass
class DraftEdge:
    source_ref: str
    target_ref: str
    type: EdgeType


@dataclass
class PICGraph:
    """한 문답에서 뽑아낸 후보 그래프 (아직 폴립층)."""

    nodes: list[DraftNode] = field(default_factory=list)
    edges: list[DraftEdge] = field(default_factory=list)
    summary: str = ""
    #: LLM 구조화가 아니라 규칙 기반 대체로 만들어졌는가
    fallback: bool = False

    def by_type(self, type: NodeType) -> list[DraftNode]:
        return [n for n in self.nodes if n.type is type]

    @property
    def conclusions(self) -> list[DraftNode]:
        return self.by_type(NodeType.CONCLUSION)


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "이 문답이 확립한 것을 한 문장으로.",
        },
        "premises": {
            "type": "array",
            "description": "답이 의존하는 전제. 답에 명시되지 않았어도 필요한 것을 드러낸다.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["title", "content"],
                "additionalProperties": False,
            },
        },
        "inferences": {
            "type": "array",
            "description": "전제에서 결론으로 가는 추론 단계.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["title", "content"],
                "additionalProperties": False,
            },
        },
        "conclusions": {
            "type": "array",
            "description": "이 문답이 실제로 주장하는 결론. 보통 1~2개.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["title", "content"],
                "additionalProperties": False,
            },
        },
        "concepts": {
            "type": "array",
            "description": "재사용 가능한 개념 이름들 (짧게).",
            "items": {"type": "string"},
        },
        "unresolved": {
            "type": "array",
            "description": "명제로 담기지 않은 것 — 절차적·감각적·암묵적 부분. 없으면 빈 배열.",
            "items": {"type": "string"},
        },
    },
    "required": [
        "summary",
        "premises",
        "inferences",
        "conclusions",
        "concepts",
        "unresolved",
    ],
    "additionalProperties": False,
}

_PROMPT = """다음 인간-AI 문답을 촘스키식 P→I→C(전제 → 추론 → 결론) 구조로 분해해라.

규칙:
- 답에 **명시되지 않았지만 답이 의존하는** 전제를 드러내는 것이 이 작업의 핵심이다.
- 결론은 이 문답이 실제로 주장하는 것만. 일반론을 지어내지 마라.
- 명제로 담기지 않는 것(절차적 요령·감각적 판단·암묵지)은 unresolved 에 적어라.
  담기지 않는다는 사실 자체가 보존해야 할 정보다.
- 각 항목은 그 자체로 읽히도록 써라. 다른 항목을 "위에서 말한" 식으로 참조하지 마라.

[질문]
{question}

[기저 LLM 의 답]
{answer}
"""


def capture(llm: BaseLLM, question: str, answer: str) -> PICGraph:
    """문답 → 후보 그래프. 실패 시 규칙 기반으로 떨어진다 (증발 방지 우선)."""
    prompt = _PROMPT.format(question=question, answer=answer)
    try:
        raw = llm.extract(prompt, _SCHEMA)
    except Exception as exc:
        log.warning("P→I→C 구조화 실패, 규칙 기반 대체: %s", exc)
        raw = {}
    if not raw:
        return _fallback(question, answer)
    return _assemble(raw)


def _assemble(raw: dict[str, Any]) -> PICGraph:
    graph = PICGraph(summary=str(raw.get("summary", "")).strip())
    counter = {"p": 0, "i": 0, "c": 0, "k": 0}

    def add(type: NodeType, prefix: str, title: str, content: str) -> DraftNode:
        counter[prefix] += 1
        node = DraftNode(
            type=type,
            title=title.strip()[:200],
            content=content.strip(),
            ref=f"{prefix}{counter[prefix]}",
        )
        graph.nodes.append(node)
        return node

    premises = [
        add(NodeType.PREMISE, "p", item.get("title", ""), item.get("content", ""))
        for item in raw.get("premises", [])
        if item.get("title")
    ]
    inferences = [
        add(NodeType.INFERENCE, "i", item.get("title", ""), item.get("content", ""))
        for item in raw.get("inferences", [])
        if item.get("title")
    ]
    conclusions = [
        add(NodeType.CONCLUSION, "c", item.get("title", ""), item.get("content", ""))
        for item in raw.get("conclusions", [])
        if item.get("title")
    ]
    for name in raw.get("concepts", []):
        if str(name).strip():
            add(NodeType.CONCEPT, "k", str(name), "")

    # P →infers→ I →infers→ C 사슬. 배당 라우팅(ancestors)이 이 사슬을 탄다.
    for premise in premises:
        for inference in inferences or conclusions:
            graph.edges.append(
                DraftEdge(premise.ref, inference.ref, EdgeType.INFERS)
            )
    for inference in inferences:
        for conclusion in conclusions:
            graph.edges.append(
                DraftEdge(inference.ref, conclusion.ref, EdgeType.INFERS)
            )

    unresolved = [str(u).strip() for u in raw.get("unresolved", []) if str(u).strip()]
    if unresolved:
        # 암묵지 병목을 삭제하지 않고 기록으로 남긴다 (spec §4 한계).
        graph.summary += "\n\n[명제로 담기지 않은 것] " + " / ".join(unresolved)
    return graph


_SENTENCE = re.compile(r"(?<=[.!?。？！])\s+|\n+")


def _fallback(question: str, answer: str) -> PICGraph:
    """LLM 구조화가 불가능할 때 — 최소한 문답은 증발시키지 않는다.

    구조를 지어내지 않고 결론 하나만 세운다. 폴립층에 "구조화 미완"으로
    남아 사용자가 손으로 증분할 수 있다.
    """
    sentences = [s.strip() for s in _SENTENCE.split(answer) if s.strip()]
    head = sentences[0] if sentences else answer.strip()[:200]
    graph = PICGraph(
        summary="자동 구조화 미완 — 사용자 증분 필요",
        fallback=True,
    )
    graph.nodes.append(
        DraftNode(
            type=NodeType.CLAIM,
            title=head[:200] or question[:200],
            content=answer.strip(),
            ref="c1",
        )
    )
    return graph
