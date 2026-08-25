"""기저 LLM 제보자 — **판정권 없음.**

이 모듈은 후보와 정본 사이의 잠재 충돌을 **사람 눈앞에 올리기만** 한다.
반환 타입이 :class:`app.core.promotion.Advisory` 인 것이 그 계약이다 —
손상 판정이 읽는 :class:`Conflict` 와 타입이 갈라져 있고, 둘 사이의 변환은
코드베이스 어디에도 없다. 제보를 판정으로 승격시키려면 새 코드를 써야 하고,
그때 이 주석이 왜 그러면 안 되는지 말해줄 것이다.

⚠ 왜 이렇게까지 하나 (중력 5 기록): v0 에서는 이 프로브가 판정자였다.
설계 어디에도 없던 구조였고, §B5 가 요구한 "은폐된 편집 판단의 외재화·분산"의
역방향이었다. 같은 병이 dapsol(`opinions/evaluate`)에서 독립 재발한 것이
확인되었으므로 — LLM 판정자는 실수가 아니라 **끌림**이다 — 타입으로 막는다.

남는 위험(정직하게): 무엇을 사람 눈앞에 올릴지 고르는 것 자체가 조용한 의제
설정 권력이다. "요청 시에만 제보 + 전체 로그 공개"로 최소화할 뿐 소멸하지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any

from app.capture.llm import BaseLLM
from app.core.ontology import Node
from app.core.promotion import Advisory

log = logging.getLogger(__name__)

#: 정본이 커도 한 번에 시험하는 상대는 이만큼으로 자른다 (컨텍스트 예산).
MAX_CANONICAL_PER_PROBE = 30

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "conflicts": {
            "type": "array",
            "description": "후보와 정본 항목 사이의 **모순**만. 새롭다/다르다는 모순이 아니다.",
            "items": {
                "type": "object",
                "properties": {
                    "canonical_id": {
                        "type": "string",
                        "description": "충돌하는 정본 항목의 id (목록에 주어진 그대로).",
                    },
                    "detail": {
                        "type": "string",
                        "description": "둘이 동시에 참일 수 없는 이유를 한 문장으로.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "0.0~1.0. 애매하면 낮게.",
                    },
                },
                "required": ["canonical_id", "detail", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["conflicts"],
    "additionalProperties": False,
}

_PROMPT = """아래 **후보 지식**과 이 시스템의 **검증된 정본** 사이에 모순이 있어 보이면
사람이 살펴볼 수 있도록 알려라. 판정은 사람이 한다 — 너는 후보를 지목할 뿐이다.

판정 규칙 (엄격히 지켜라):
- 손상 = 후보와 정본 항목이 **동시에 참일 수 없다**는 것. 그것만 conflicts 에 넣어라.
- **낯설다·새롭다·정본에 없다**는 손상이 아니다. 새로운 지식은 오히려 이 시스템이
  원하는 것이다. 정본에 없다는 이유로 절대 conflict 를 만들지 마라.
- 정본을 **더 정밀하게 만드는** 후보(예외 조건 추가, 범위 한정, 세분화)도 손상이
  아니다. 기존 주장을 뒤집을 때만 손상이다.
- 애매하면 conflict 를 만들지 말고, 만들더라도 confidence 를 낮게 매겨라.
- 진짜 모순이 하나도 없으면 conflicts 를 빈 배열로 반환해라. 그게 정상적인 결과다.

[후보 지식]
id: {candidate_id}
종류: {candidate_type}
제목: {candidate_title}
내용: {candidate_content}

[검증된 정본]
{canonical_block}
"""

#: 이보다 낮은 확신의 제보는 사람에게 보여주지 않는다 (소음 억제).
#: 판정 문턱이 아니다 — 제보는 애초에 판정에 들어가지 않는다.
CONFIDENCE_FLOOR = 0.6


def make_advisor(llm: BaseLLM, *, confidence_floor: float = CONFIDENCE_FLOOR):
    """충돌 후보를 제보하는 함수를 만든다. 판정 경로에는 연결되지 않는다."""

    def advise(candidate: Node, canonical: list[Node]) -> list[Advisory]:
        subset = canonical[:MAX_CANONICAL_PER_PROBE]
        if not subset:
            return []
        canonical_block = "\n".join(
            f"- id: {n.id} | [{n.type.value}] {n.title}"
            + (f" — {n.content[:300]}" if n.content else "")
            for n in subset
        )
        prompt = _PROMPT.format(
            candidate_id=candidate.id,
            candidate_type=candidate.type.value,
            candidate_title=candidate.title,
            candidate_content=candidate.content[:2000] or "(내용 없음)",
            canonical_block=canonical_block,
        )
        raw = llm.extract(prompt, _SCHEMA)
        valid_ids = {n.id for n in subset}
        advisories: list[Advisory] = []
        for item in raw.get("conflicts", []):
            cid = str(item.get("canonical_id", "")).strip()
            if cid not in valid_ids:
                # 존재하지 않는 정본을 지목한 제보는 버린다 (환각 방어).
                log.warning("제보자가 미지의 정본 id 를 지목: %r", cid)
                continue
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.0) or 0.0)))
            if confidence < confidence_floor:
                continue
            advisories.append(
                Advisory(
                    candidate_id=candidate.id,
                    canonical_id=cid,
                    detail=str(item.get("detail", "")).strip() or "충돌 후보 (사유 미기재)",
                    confidence=confidence,
                    model=getattr(llm, "name", ""),
                )
            )
        return advisories

    return advise
