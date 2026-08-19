"""컨텍스트 주입 — 승격 (a)경로가 다음 문답으로 환류하는 지점.

**한 바퀴가 닫히는 곳이 여기다.** 정본에 굳은 지식이 다음 질문의 답에 실제로
참조되지 않으면, 승격은 장부상의 사건일 뿐 시스템은 아무것도 학습하지 않은
것이다.

검색 방식은 의도적으로 원시적이다 — 토큰 겹침 + 권위 가중. 임베딩을 쓰지 않는
이유는 두 가지다: (1) MVP 는 *한 바퀴가 도는가*를 증명하는 것이지 검색 품질
경연이 아니고, (2) spec §C1 이 지적한 **검색 병목**("검색기는 모델만큼 관련성을
이해하지 못한다")은 임베딩으로도 안 풀리는 RAG 의 구조적 한계라서 여기서 공들일
자리가 아니다. 이 한계는 승격 (b)경로(가중치 체화)가 풀어야 할 몫으로 남긴다.
"""

from __future__ import annotations

import re

from app.core.ontology import Node, NodeType

#: 검색에서 뺄 조사·기능어. 한국어는 형태소 분석 없이 접미 매칭으로 근사한다.
_STOP = {
    "그리고", "그러나", "하지만", "따라서", "때문에", "위해", "대한", "관한",
    "the", "a", "an", "of", "to", "in", "is", "are", "and", "or", "for", "on",
    "what", "how", "why", "이란", "무엇", "어떻게", "왜",
}

_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")

#: 결론·주장이 개념보다 주입 가치가 높다 (결론은 그 자체로 지식, 개념은 이름표).
_TYPE_WEIGHT = {
    NodeType.CONCLUSION: 1.5,
    NodeType.CLAIM: 1.3,
    NodeType.INFERENCE: 1.1,
    NodeType.EVIDENCE: 1.1,
    NodeType.PREMISE: 1.0,
    NodeType.CONCEPT: 0.8,
    NodeType.QA: 0.8,
    NodeType.SOURCE: 0.6,
}


def tokenize(text: str) -> set[str]:
    tokens = set()
    for raw in _TOKEN.findall(text.lower()):
        if len(raw) < 2 or raw in _STOP:
            continue
        tokens.add(raw)
        # 한국어 조사 근사: 어간 앞부분도 후보로 넣어 '가소성이'/'가소성' 을 잇는다.
        if len(raw) > 3:
            tokens.add(raw[:-1])
    return tokens


def relevance(question_tokens: set[str], node: Node) -> float:
    """질문과 노드의 토큰 겹침 (0.0 이면 무관)."""
    node_tokens = tokenize(f"{node.title} {node.content}")
    if not node_tokens or not question_tokens:
        return 0.0
    overlap = question_tokens & node_tokens
    if not overlap:
        return 0.0
    return len(overlap) / (len(question_tokens) ** 0.5)


def select(
    question: str,
    canonical: list[Node],
    *,
    authority: dict[str, float] | None = None,
    top_k: int = 8,
) -> list[Node]:
    """질문에 주입할 정본 노드를 고른다.

    점수 = 토큰 겹침 × 타입 가중 × (1 + 권위). 권위가 곱해지는 이유: 좋은 안목의
    평가자들이 일찍 인정한 노드가 먼저 불려나와야 한다 (scoring.py 의 통화는
    인기가 아니라 안목이다).
    """
    authority = authority or {}
    question_tokens = tokenize(question)
    scored: list[tuple[float, Node]] = []
    for node in canonical:
        base = relevance(question_tokens, node)
        if base <= 0:
            continue
        weight = _TYPE_WEIGHT.get(node.type, 1.0)
        score = base * weight * (1.0 + authority.get(node.id, 0.0))
        scored.append((score, node))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [node for _score, node in scored[:top_k]]


def render(nodes: list[Node]) -> str:
    """주입 블록 텍스트. 기저에는 **텍스트로만** 들어간다 (산출물 층위 접합)."""
    if not nodes:
        return ""
    lines = []
    for node in nodes:
        line = f"- [{node.type.value}] {node.title}"
        if node.content:
            line += f"\n    {node.content.strip()[:600]}"
        lines.append(line)
    return "\n".join(lines)
