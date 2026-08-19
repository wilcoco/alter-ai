# 이식 출처: github.com/wilcoco/CAMS-KnowledgeNet
#            (branch claude/ontology-knowledge-evaluation-uzuDx)
#            src/nightwish/verification.py — 의존성 0 으로 설계되어 그대로 옮겼다.
# 수정 시 상류와의 차이를 여기에 적을 것.
"""외부 현실 닻 (ground-truth verification) — 로드맵 P0 / critique §1.1, §2, §6.

비평의 결론: **"동의가 유일한 가치 원천이면, 동의는 조작될 수 있다."** 이
순환을 닫는 유일한 길은 *외부의 검증 가능한 현실*이다. 제조 도메인에서는
"이 답이 수율을 올렸는가 / 불량률을 내렸는가"가 담합으로 바뀌지 않는다.

이 모듈은 노드를 **측정 가능한 결과**에 연결한다:

* :class:`Measurement` — 한 노드가 주장한 효과의 실측치(기준값 → 관측값).
* :class:`VerificationRegistry` — 노드별 측정 기록과 검증 여부.

핵심 사용처: :func:`app.core.economy.Economy.distribute_dividend` 의
``is_verified`` 게이트. 검증 닻이 없는 가지에서는 배당을 끄면, 시스템이
"정교한 폰지와 구별 불가"해지는 상태(critique §2)를 구조적으로 차단한다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum


class Direction(str, Enum):
    """좋은 방향. 수율은 클수록, 불량률은 작을수록 좋다."""

    HIGHER_BETTER = "higher_better"   # 수율, 물성 강도 등
    LOWER_BETTER = "lower_better"     # 불량률, 싸이클타임 등


@dataclass(frozen=True)
class Measurement:
    """한 노드가 주장한 개선의 실측치.

    예: 웰드라인 불량률을 8.0% → 2.0% 로 낮췄다 (LOWER_BETTER, 최소 20% 개선 요구).
    """

    metric: str
    baseline: float
    observed: float
    direction: Direction = Direction.HIGHER_BETTER
    unit: str = ""
    #: 검증으로 인정할 최소 *상대* 개선폭 (0.20 = 20% 개선)
    min_rel_improvement: float = 0.0

    @property
    def relative_improvement(self) -> float:
        """기준값 대비 상대 개선폭 (양수 = 개선). 0으로 나눔은 절대차로 대체."""
        if self.direction is Direction.HIGHER_BETTER:
            delta = self.observed - self.baseline
        else:
            delta = self.baseline - self.observed
        denom = abs(self.baseline)
        return delta / denom if denom > 1e-12 else delta

    @property
    def passes(self) -> bool:
        """실측 개선이 요구 임계를 넘었는가 (= 외부 현실이 답을 인정했는가).

        반드시 *실제* 개선이 있어야(>0) 하고, 요구 임계 이상이어야 한다. 변화
        없음(0)이나 악화(<0)는 검증 실패.
        """
        imp = self.relative_improvement
        return imp > 0 and imp >= self.min_rel_improvement


@dataclass
class VerificationRegistry:
    """노드별 측정 기록. '검증됨' = 통과한 측정이 하나라도 있음."""

    results: dict[str, list[Measurement]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def record(self, node_id: str, measurement: Measurement) -> bool:
        """노드에 측정을 기록하고, 그 측정의 통과 여부를 반환."""
        self.results[node_id].append(measurement)
        return measurement.passes

    def is_verified(self, node_id: str) -> bool:
        """이 노드에 통과한 측정이 하나라도 있는가."""
        return any(m.passes for m in self.results.get(node_id, ()))

    def branch_verified(self, node_id: str, ancestor_ids: list[str]) -> bool:
        """노드 자신 또는 조상 중 하나라도 검증되었는가 (검증된 가지)."""
        if self.is_verified(node_id):
            return True
        return any(self.is_verified(a) for a in ancestor_ids)

    def verified_predicate(self):
        """``distribute_dividend(is_verified=...)`` 에 넣을 술어를 만든다."""
        return self.is_verified
