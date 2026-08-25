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


def search(
    question: str,
    nodes: list[Node],
    *,
    authority: dict[str, float] | None = None,
    top_k: int = 12,
    explore_quota: float = 0.30,
) -> tuple[list[Node], set[str]]:
    """검색 선점 — **LLM 을 부르기 전에** 기존 문답을 먼저 보여준다.

    주입(:func:`select`)과 다른 점 두 가지:

    * **정본뿐 아니라 폴립도** 결과에 넣는다. 폴립은 노출되지 않으면 판단받을
      기회 자체가 없고, 그러면 관문에 증거가 안 들어온다. **노출면이 곧
      심사대다** (spec §3).
    * **탐험 쿼터** — 결과의 일정 비율을 신규·잠복·저인정 노드에 강제 배정한다.
      인기 노드만 노출되면 비인기 폴립은 영원히 계류하는 마태 효과가 생긴다.
      "라우팅이 곧 기회이고 기회가 곧 소득이면, 라우팅 분류기는 사실상
      분배기다" (H2A2H2 anti-Matthew 이식).

    반환: (결과 노드, 탐험 슬롯으로 들어온 노드 id 집합).
    """
    authority = authority or {}
    question_tokens = tokenize(question)
    scored: list[tuple[float, Node]] = []
    for node in nodes:
        base = relevance(question_tokens, node)
        if base <= 0:
            continue
        # 주입에서는 결론이 중하지만 검색에서는 **문답 자체가 1급 결과**다 —
        # 사용자가 찾는 것은 "누가 무엇을 묻고 무엇을 얻었는가"이기 때문.
        weight = 1.4 if node.type is NodeType.QA else _TYPE_WEIGHT.get(node.type, 1.0)
        # 검증된 것이 먼저 보이되, 폴립이 배제되지는 않는다.
        status_boost = 1.4 if node.is_canonical else 1.0
        score = base * weight * status_boost * (1.0 + authority.get(node.id, 0.0))
        scored.append((score, node))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))

    ranked = [node for _s, node in scored]
    n_explore = min(int(top_k * explore_quota), max(0, len(ranked) - 1))
    if n_explore <= 0:
        return ranked[:top_k], set()

    # 상위권에 이미 든 것은 탐험 대상이 아니다 — 아래쪽에서 저인정 순으로 뽑는다.
    head = ranked[: top_k - n_explore]
    head_ids = {n.id for n in head}
    tail = [n for n in ranked if n.id not in head_ids]
    # 인정이 낮은 것 우선, 같으면 **새 것 우선** — 아직 판단받을 기회가 없었던
    # 노드를 먼저 올린다. 오래도록 무시된 노드는 여기서 경쟁시키지 않고
    # "밀려난 답"(잠복) 표면이 따로 맡는다.
    tail.sort(key=lambda n: (authority.get(n.id, 0.0), -n.created_at))
    explore = tail[:n_explore]
    return head + explore, {n.id for n in explore}


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
