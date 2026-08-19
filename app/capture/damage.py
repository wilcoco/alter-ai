"""의미적 손상 프로브 — :mod:`app.core.promotion` 에 주입되는 v0 구현.

관문의 질문은 "낯선가"가 아니라 **"통합하면 정본이 손상되는가"**다. 그래서
프롬프트가 novelty 를 묻지 않는다. 오직 *모순*만 묻는다.

⚠ 이 프로브의 판정자는 기저 LLM 이다. 즉 **관문이 기저의 판단에 의존한다**는
순환이 여기 남아 있다 (spec §6-3 "관문의 적응 학습 재귀"의 한 얼굴). 외부 현실
닻(verification.py)만이 이 순환 밖에 있으며, 그래서 실측이 통과하면 스테이크
문턱을 면제하도록 정책이 짜여 있다. 프로브를 개선해도 이 순환은 안 풀린다.
"""

from __future__ import annotations

import logging
from typing import Any

from app.capture.llm import BaseLLM
from app.core.ontology import Node
from app.core.promotion import Conflict

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

_PROMPT = """아래 **후보 지식**을 이 시스템의 **검증된 정본**에 통합하려 한다.
통합했을 때 정본이 손상되는지만 판정해라.

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

#: 이보다 낮은 확신의 충돌은 손상으로 세지 않는다. 음성 선택의 보수성 편향
#: (쿤 문제, spec §6-2)을 조금이라도 눅이려는 완충 — 해결이 아니라 완충이다.
CONFIDENCE_FLOOR = 0.6


def make_probe(llm: BaseLLM, *, confidence_floor: float = CONFIDENCE_FLOOR):
    """:func:`app.core.promotion.judge` 에 넘길 ``DamageProbe`` 를 만든다."""

    def probe(candidate: Node, canonical: list[Node]) -> list[Conflict]:
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
        conflicts: list[Conflict] = []
        for item in raw.get("conflicts", []):
            cid = str(item.get("canonical_id", "")).strip()
            if cid not in valid_ids:
                # 존재하지 않는 정본을 지목한 판정은 버린다 (환각 방어).
                log.warning("손상 프로브가 미지의 정본 id 를 지목: %r", cid)
                continue
            confidence = float(item.get("confidence", 0.0) or 0.0)
            if confidence < confidence_floor:
                continue
            conflicts.append(
                Conflict(
                    canonical_id=cid,
                    kind="semantic",
                    detail=str(item.get("detail", "")).strip() or "모순 (사유 미기재)",
                    confidence=confidence,
                )
            )
        return conflicts

    return probe
