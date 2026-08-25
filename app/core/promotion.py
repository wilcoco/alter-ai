"""석회화 판정 기관 — 승격 관문 (v1.3).

spec §2: **검증 닻 + 유인합치 시장 + 면역 관문은 별개 부품이 아니라 하나의
석회화 판정 기관이다.** 이 모듈이 그 하나다.

------------------------------------------------------------------------------
원칙 (docs/promotion-gate-spec.md §1)

    사람은 증거를 제출한다 — 반증, 실측치, 스테이크.
    승격 판정은 그 증거에 대해 **공개된 결정론적 함수**가 내린다.
    기저 LLM 은 관문 밖의 제보자다 — 표가 없다.

세 절이 각각 함정 하나씩을 막는다: "사람이 직접 판정"이면 인기투표(대리변수
금지 위반), "함수가 비공개"면 은폐된 편집 노동의 재현(§B5 위반), "LLM 이
판정"이면 중력 5.

⚠ v0 에서 무엇이 잘못이었나 (중력 5): 이 모듈의 이전 판본은 기저 LLM 이
의미적 모순을 판정해 그 결과가 곧바로 잠복으로 이어졌다. 설계 어디에도 없는
구조였고, §B5 가 요구한 "은폐된 편집 판단의 외재화·분산"의 정확히 역방향이었다.
같은 병이 dapsol(`opinions/evaluate` — LLM 이 채점하고 시스템 계정이 포인트를
발행)에서 독립적으로 재발한 것이 확인되었다. LLM 판정자는 개인의 실수가 아니라
**끌림(attractor)**이므로, 이 모듈은 프로토콜로도 그것을 막는다 — :class:`Advisory`
는 판정 경로에 입력될 수 없는 별도 타입이다.
------------------------------------------------------------------------------

판정 기준은 novelty 가 아니라 **손상**이다 (danger model, Matzinger). 관문이
던지는 질문은 "낯선가"가 아니라 "통합하면 기존 자기가 손상되는가"다. 낯섦은
오히려 학습 가치이므로 감점 요인이 아니다.

여기서 **자기(self) = 이미 승격된 정본 온톨로지**로 정의한다. 이것은 spec §6-4
"자기의 계산적 정의"의 *부분* 답일 뿐이다 — 능력 차원의 자기는 여전히 미해결.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from app.core.ontology import Node, NodeStatus, Ontology
from app.core.verification import VerificationRegistry


class Verdict(str, Enum):
    PROMOTE = "promote"          # 승격 — 신규 안정층으로
    HOLD = "hold"                # 보류 — 아직 근거 부족, 폴립층 유지
    DAMAGE_REJECT = "damage"     # 손상 — 잠복으로 (삭제 아님)


class GateReason(str, Enum):
    """판정 이유 코드.

    거절에 이유를 붙이는 것은 친절이 아니라 §1 "공개된 함수" 원칙의 일부다 —
    사용자가 "무엇을 더 하면 되는지" 알 수 없으면 관문은 블랙박스이고, 그건
    은폐된 편집 노동과 구별되지 않는다. (H2A2H2 ``gateReason`` 이식.)
    """

    OK = "ok"
    ALREADY_CANONICAL = "already_canonical"
    DAMAGED_BY_CANONICAL_REFUTER = "damaged_by_canonical_refuter"
    DAMAGED_BY_REALITY = "damaged_by_reality"
    ANCHORED = "anchored"
    RECOGNITION_BELOW_THRESHOLD = "recognition_below_threshold"
    QUARANTINE_PENDING = "quarantine_pending"


@dataclass(frozen=True)
class Conflict:
    """후보가 정본의 어느 노드와 어떻게 부딪히는가 — **사람이 만든 증거**."""

    canonical_id: str
    kind: str          # "refutes_edge" | "reality" | ...
    detail: str


@dataclass(frozen=True)
class Advisory:
    """기저 LLM 의 제보 — **판정 경로에 입력되지 않는다.**

    :class:`Conflict` 와 의도적으로 다른 타입이다. 타입이 갈라져 있으므로 제보를
    실수로 손상 신호에 섞으려면 명시적 변환을 써야 하고, 그런 변환은 이 모듈에
    존재하지 않는다. 제보는 사람 눈앞에 올라가 반증의 재료가 될 뿐이다.

    남는 위험(정직하게): 무엇을 사람 눈앞에 올릴지 고르는 것 자체가 조용한
    의제 설정 권력이다. v1.3 은 "요청 시에만 제보 + 전체 로그 공개"로 최소화할
    뿐 소멸시키지 못한다.
    """

    candidate_id: str
    canonical_id: str
    detail: str
    confidence: float
    model: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 는 0.0~1.0")


@dataclass
class DamageReport:
    """손상 시험 결과 (음성 선택의 출력). 입력은 전부 사람·현실이 만든 것이다."""

    conflicts: list[Conflict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def damaged(self) -> bool:
        return bool(self.conflicts)

    @property
    def kinds(self) -> set[str]:
        return {c.kind for c in self.conflicts}

    def summary(self) -> str:
        if not self.conflicts:
            return "손상 없음 — 검증된 반증 및 실측 반증 미발견"
        return "; ".join(f"{c.canonical_id}: {c.detail}" for c in self.conflicts)


@dataclass(frozen=True)
class PromotionPolicy:
    """승격 문턱값. 전부 환경변수로 조정 가능 (app.config)."""

    #: 승격에 필요한 최소 누적 인정 지수 (순서·허브 가중 권위 — 특허 10-0913256)
    recognition_threshold: float = 25.0
    #: 잠복기 — 노출된 채 손상 없이 살아남아야 하는 최소 관찰 단위
    quarantine_ticks: int = 1
    #: 검증 닻(외부 실측)이 통과했으면 인정 문턱과 잠복기를 면제한다.
    #: 근거: 실측은 동의보다 강한 신호다 (spec §7 "가격 신호: 사후 실측 가치").
    anchor_bypasses_recognition: bool = True


@dataclass
class PromotionDecision:
    verdict: Verdict
    reason_code: GateReason
    reason: str
    damage: DamageReport
    #: 관문 지표 — 누적 인정 지수 (원시 스테이크가 아니다)
    recognition: float
    #: 경제적 잠금. 관문 지표는 아니지만 감사 레코드에 남는다
    stake: float
    anchored: bool
    observed_ticks: int
    #: 계류 중인 반증이 있는가 — 판정을 **차단하지 않고** 동행한다
    challenged: bool = False

    @property
    def promoted(self) -> bool:
        return self.verdict is Verdict.PROMOTE


def judge(
    candidate: Node,
    ontology: Ontology,
    *,
    recognition: float,
    stake: float,
    registry: VerificationRegistry,
    policy: PromotionPolicy,
    challenged: bool = False,
) -> PromotionDecision:
    """한 후보 노드에 대한 석회화 판정. **순서가 설계다.**

    ① 손상 시험 (음성 선택) → 잠복. 인정이 아무리 쌓여도 자기를 손상시키는
       지식은 통과하지 못한다 — 망각 방지와 오염 방어는 같은 메커니즘이라는
       §7 원칙의 구현.
    ② 실측 검증 통과 → 승격. 실측 > 동의.
    ③ 누적 인정 미달 → 보류 (기각이 아니다).
    ④ 잠복기 미경과 → 보류. 시간이 검증자.
    ⑤ 통과 → 승격.

    ``challenged``(계류 반증 존재)는 어느 단도 차단하지 않는다. 판정에 동행해
    노출·주입 시 ⚠ 로 표기될 뿐이고, 그 반증이 스스로 관문을 통과하는 순간
    ①(a)에 걸려 대상이 재심된다. 근거와 트레이드오프: spec §5.

    이 함수는 LLM 을 호출하지 않으며 호출할 수도 없다 — 인자에 그런 통로가 없다.
    """

    def decide(
        verdict: Verdict, code: GateReason, reason: str, damage: DamageReport
    ) -> PromotionDecision:
        return PromotionDecision(
            verdict=verdict,
            reason_code=code,
            reason=reason,
            damage=damage,
            recognition=recognition,
            stake=stake,
            anchored=registry.is_verified(candidate.id),
            observed_ticks=candidate.observed_ticks,
            challenged=challenged,
        )

    if candidate.status is NodeStatus.CANONICAL:
        # 정본도 재심 대상이다 — 손상이면 끌어내린다 ("느리게 사는 층").
        damage = damage_test(candidate, ontology, registry)
        if damage.damaged:
            return decide(
                Verdict.DAMAGE_REJECT,
                _damage_code(damage),
                f"정본 재심 — 손상: {damage.summary()}",
                damage,
            )
        return decide(
            Verdict.PROMOTE, GateReason.ALREADY_CANONICAL, "이미 정본 (재심 통과)", damage
        )

    damage = damage_test(candidate, ontology, registry)

    if damage.damaged:
        return decide(
            Verdict.DAMAGE_REJECT,
            _damage_code(damage),
            f"손상 판정: {damage.summary()}",
            damage,
        )

    if registry.is_verified(candidate.id) and policy.anchor_bypasses_recognition:
        return decide(
            Verdict.PROMOTE,
            GateReason.ANCHORED,
            "외부 현실 닻 통과 — 실측이 동의를 대체한다",
            damage,
        )

    if recognition < policy.recognition_threshold:
        return decide(
            Verdict.HOLD,
            GateReason.RECOGNITION_BELOW_THRESHOLD,
            (
                f"누적 인정 부족: {recognition:.1f} < "
                f"{policy.recognition_threshold:.1f} (또는 실측 닻 필요)"
            ),
            damage,
        )

    if candidate.observed_ticks < policy.quarantine_ticks:
        return decide(
            Verdict.HOLD,
            GateReason.QUARANTINE_PENDING,
            (
                f"잠복기 미경과: {candidate.observed_ticks}/"
                f"{policy.quarantine_ticks} (노출된 채 손상 없이 살아남는 중)"
            ),
            damage,
        )

    return decide(
        Verdict.PROMOTE,
        GateReason.OK,
        (
            f"누적 인정 {recognition:.1f} ≥ {policy.recognition_threshold:.1f} · "
            f"잠복기 {candidate.observed_ticks} 경과 · 손상 없음"
        ),
        damage,
    )


def _damage_code(report: DamageReport) -> GateReason:
    if "reality" in report.kinds:
        return GateReason.DAMAGED_BY_REALITY
    return GateReason.DAMAGED_BY_CANONICAL_REFUTER


def damage_test(
    candidate: Node, ontology: Ontology, registry: VerificationRegistry
) -> DamageReport:
    """음성 선택 — 후보를 "보호 대상"(정본)에 시험 발사한다.

    손상 신호는 두 종류뿐이고 **둘 다 기저 LLM 밖에서 온다**:

    1. **검증된 반증** — *정본이 된* 반증 노드가 후보를 ``refutes`` 로 겨눈다.
       아직 폴립인 반증은 손상이 아니다 ("반증했다"가 아니라 "반증이
       검증됐다"가 손상). 계류 반증은 ``challenged`` 로 동행할 뿐이다.
    2. **실측 반증** — 외부 실측이 *악화*를 보였다. 변화 없음·임계 미달은
       "검증 안 됨"이지 반증이 아니다.

    ⚠ 여전히 미해결 (spec §6-1, §6-2): 이것은 손상의 *존재*를 "검증된 반증의
    존재"로 치환한 것이지 손상의 *정도*를 재는 함수가 아니다. 그리고 반증이
    관문을 통과하려면 결국 인정이나 실측이 필요하므로, 음성 선택의 보수성
    편향(쿤 문제)은 사람 시장 안으로 자리를 옮겼을 뿐 사라지지 않았다.
    이 함수를 고칠 때 "손상 판정을 풀었다"고 쓰지 말 것.
    """
    report = DamageReport()

    # (1) 검증된 반증: 정본이 후보를 refutes 로 겨눈다.
    for edge in ontology.refuters_of(candidate.id):
        source = ontology.nodes.get(edge.source_id)
        if source is None or source.status is not NodeStatus.CANONICAL:
            continue
        report.conflicts.append(
            Conflict(
                canonical_id=source.id,
                kind="refutes_edge",
                detail=f"검증된 반증 «{source.title}» 가 이 후보를 반박한다",
            )
        )

    # (2) 실측 반증: 현실이 악화를 보였다.
    for measurement in registry.results.get(candidate.id, ()):
        if measurement.relative_improvement < 0:
            report.conflicts.append(
                Conflict(
                    canonical_id=candidate.id,
                    kind="reality",
                    detail=(
                        f"실측 악화 — {measurement.metric}: "
                        f"{measurement.baseline} → {measurement.observed}"
                    ),
                )
            )

    if not report.conflicts:
        report.notes.append(
            "손상 신호원은 검증된 반증과 실측뿐 — 기저 LLM 은 판정에 관여하지 않는다."
        )
    return report


def apply(
    decision: PromotionDecision,
    node: Node,
    ontology: Ontology,
    *,
    on_dormant: Callable[[str], None] | None = None,
) -> Node:
    """판정을 온톨로지에 반영한다.

    **판정은 반드시 상태를 바꾼다** — 계산만 하고 쓰지 않는 게이트를 금지하는
    구현 규율(spec §6-3). 전작 두 곳에서 이 배선이 빠져 게이트가 장식이 됐다.
    """
    if decision.verdict is Verdict.PROMOTE:
        if node.status is NodeStatus.CANONICAL:
            return node
        return ontology.promote(node.id, decision.reason)

    if decision.verdict is Verdict.DAMAGE_REJECT:
        result = ontology.make_dormant(node.id, decision.reason)
        if on_dormant is not None:
            on_dormant(node.id)   # 잠긴 포인트를 유동성 풀로 환원 (소각 아님)
        return result

    node.observed_ticks += 1
    node.verdicts.append(f"[t{ontology.clock}] HOLD — {decision.reason}")
    return node
