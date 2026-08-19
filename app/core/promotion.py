"""석회화 판정 기관 — 승격 게이트.

spec §2: **검증 닻 + 유인합치 시장 + 면역 관문은 별개 부품이 아니라 하나의
석회화 판정 기관이다.** 이 모듈이 그 하나다.

판정 기준은 novelty 가 아니라 **손상**이다 (danger model, Matzinger). 관문이
던지는 질문은 "낯선가"가 아니라 **"통합하면 기존 자기가 손상되는가"**다.
낯섦은 오히려 학습 가치이므로 감점 요인이 아니다.

여기서 **자기(self) = 이미 승격된 정본 온톨로지**로 정의한다. 이것은 spec §6-4
"자기의 계산적 정의"의 *부분* 답일 뿐이다 — 능력 차원의 자기는 여전히 미해결.

------------------------------------------------------------------------------
⚠ 미해결 경고 (CLAUDE.md: "건드리되 풀렸다고 주장하지 말 것")

:class:`DamageReport` 를 만드는 손상 판정 함수는 **v0 자리표시자**다. 지금
구현된 것은 두 신호뿐이다:

1. **구조적 신호** — 정본 노드가 후보를 ``refutes`` 로 겨누는 엣지가 있는가.
   확실하지만 커버리지가 거의 없다 (누군가 엣지를 그려줘야 한다).
2. **의미적 신호** — 주입 가능한 :class:`DamageProbe` (기본 구현은
   ``app/capture/damage.py`` 의 기저 LLM 모순 시험). 커버리지는 넓지만 판정의
   근거가 기저 LLM 의 판단이라 순환 위험이 있다.

풀리지 않은 것: 음성 선택의 보수성 편향(쿤 문제 — 패러다임 전환 지식은
정의상 기존 정본과 충돌하므로 구조적으로 기각된다), 관문의 적응 학습 재귀,
손상의 *정도*를 능력 차원에서 재는 방법. spec §6 참조. 이 모듈을 고칠 때
"손상 판정을 풀었다"고 쓰지 말 것.
------------------------------------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

from app.core.ontology import Node, NodeStatus, Ontology
from app.core.verification import VerificationRegistry


class Verdict(str, Enum):
    PROMOTE = "promote"          # 승격 — 신규 안정층으로
    HOLD = "hold"                # 보류 — 아직 근거 부족, 폴립층 유지
    DAMAGE_REJECT = "damage"     # 손상 — 잠복으로 (삭제 아님)


@dataclass(frozen=True)
class Conflict:
    """후보가 정본의 어느 노드와 어떻게 부딪히는가."""

    canonical_id: str
    kind: str          # "refutes_edge" | "semantic" | ...
    detail: str
    #: 0.0~1.0. 판정자가 이 충돌을 얼마나 확신하는가.
    confidence: float = 1.0


@dataclass
class DamageReport:
    """손상 시험 결과 (음성 선택의 출력)."""

    conflicts: list[Conflict] = field(default_factory=list)
    #: 시험이 실제로 수행됐는가. False = 판정 불능(프로브 미가동) — 통과가 아니다.
    probed: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def damaged(self) -> bool:
        return bool(self.conflicts)

    def summary(self) -> str:
        if not self.probed:
            return "손상 시험 미수행 (프로브 미가동)"
        if not self.conflicts:
            return "손상 없음 — 정본과 충돌 미발견"
        return "; ".join(f"{c.canonical_id}: {c.detail}" for c in self.conflicts)


class DamageProbe(Protocol):
    """의미적 손상 시험자. 기본 구현은 ``app/capture/damage.py``.

    core 를 의존성 0 으로 유지하기 위해 프로토콜로만 두고 주입받는다.
    """

    def __call__(self, candidate: Node, canonical: list[Node]) -> list[Conflict]: ...


@dataclass(frozen=True)
class PromotionPolicy:
    """승격 문턱값. 전부 환경변수로 조정 가능 (app.config)."""

    stake_threshold: float = 25.0
    quarantine_ticks: int = 1
    #: 검증 닻(외부 실측)이 통과했으면 스테이크 문턱과 잠복기를 면제한다.
    #: 근거: 실측은 동의보다 강한 신호다 (spec §7 "가격 신호: 사후 실측 가치").
    anchor_bypasses_stake: bool = True


@dataclass
class PromotionDecision:
    verdict: Verdict
    reason: str
    damage: DamageReport
    stake: float
    anchored: bool
    observed_ticks: int

    @property
    def promoted(self) -> bool:
        return self.verdict is Verdict.PROMOTE


def judge(
    candidate: Node,
    ontology: Ontology,
    *,
    stake: float,
    registry: VerificationRegistry,
    policy: PromotionPolicy,
    probe: DamageProbe | None = None,
) -> PromotionDecision:
    """한 후보 노드에 대한 석회화 판정.

    순서가 설계다:

    1. **먼저 손상 시험** (음성 선택). 손상이면 근거의 양과 무관하게 잠복.
       망각 방지와 오염 방어는 같은 메커니즘이라는 §7 원칙의 구현 — 스테이크가
       아무리 많이 쌓여도 자기를 손상시키는 지식은 통과하지 못한다.
    2. **그 다음 근거**. 외부 실측 닻이 통과했거나, (스테이크 문턱 + 잠복기)를
       넘겼으면 승격. 둘 다 아니면 보류(폴립층 유지) — 기각이 아니다.
    """
    if candidate.status is NodeStatus.CANONICAL:
        return PromotionDecision(
            verdict=Verdict.PROMOTE,
            reason="이미 정본",
            damage=DamageReport(probed=False),
            stake=stake,
            anchored=registry.is_verified(candidate.id),
            observed_ticks=candidate.observed_ticks,
        )

    canonical = [n for n in ontology.canonical() if n.id != candidate.id]
    damage = damage_test(candidate, canonical, ontology, probe=probe)
    anchored = registry.is_verified(candidate.id)

    if damage.damaged:
        return PromotionDecision(
            verdict=Verdict.DAMAGE_REJECT,
            reason=f"손상 판정: {damage.summary()}",
            damage=damage,
            stake=stake,
            anchored=anchored,
            observed_ticks=candidate.observed_ticks,
        )

    if anchored and policy.anchor_bypasses_stake:
        return PromotionDecision(
            verdict=Verdict.PROMOTE,
            reason="외부 현실 닻 통과 (실측이 동의를 대체)",
            damage=damage,
            stake=stake,
            anchored=True,
            observed_ticks=candidate.observed_ticks,
        )

    if stake < policy.stake_threshold:
        return PromotionDecision(
            verdict=Verdict.HOLD,
            reason=(
                f"스테이크 부족: {stake:.1f} < {policy.stake_threshold:.1f} "
                "(또는 실측 닻 필요)"
            ),
            damage=damage,
            stake=stake,
            anchored=anchored,
            observed_ticks=candidate.observed_ticks,
        )

    if candidate.observed_ticks < policy.quarantine_ticks:
        return PromotionDecision(
            verdict=Verdict.HOLD,
            reason=(
                f"잠복기 미경과: {candidate.observed_ticks}/"
                f"{policy.quarantine_ticks} 틱 (시간이 검증자)"
            ),
            damage=damage,
            stake=stake,
            anchored=anchored,
            observed_ticks=candidate.observed_ticks,
        )

    return PromotionDecision(
        verdict=Verdict.PROMOTE,
        reason=(
            f"스테이크 {stake:.1f} ≥ {policy.stake_threshold:.1f} · "
            f"잠복기 {candidate.observed_ticks}틱 경과 · 손상 없음"
        ),
        damage=damage,
        stake=stake,
        anchored=anchored,
        observed_ticks=candidate.observed_ticks,
    )


def damage_test(
    candidate: Node,
    canonical: list[Node],
    ontology: Ontology,
    *,
    probe: DamageProbe | None = None,
) -> DamageReport:
    """음성 선택 — 후보를 "보호 대상"(정본)에 시험 발사한다.

    ⚠ v0. 모듈 상단의 미해결 경고를 읽을 것.
    """
    report = DamageReport(conflicts=[], probed=True)

    # (1) 구조적 신호: 정본이 후보를 refutes 로 겨눈다.
    canonical_ids = {n.id for n in canonical}
    for edge in ontology.refuters_of(candidate.id):
        if edge.source_id in canonical_ids:
            src = ontology.nodes[edge.source_id]
            report.conflicts.append(
                Conflict(
                    canonical_id=edge.source_id,
                    kind="refutes_edge",
                    detail=f"정본 «{src.title}» 가 이 후보를 반박(refutes)한다",
                )
            )

    # (2) 의미적 신호: 주입된 프로브 (없으면 미수행으로 표시 — 통과가 아니다).
    if probe is None:
        report.probed = bool(report.conflicts)
        report.notes.append(
            "의미적 손상 프로브 미가동 — 구조적(refutes 엣지) 신호만 봤다. "
            "커버리지 낮음."
        )
    elif not canonical:
        report.notes.append("정본이 비어 있음 — 손상시킬 자기가 아직 없다.")
    else:
        try:
            report.conflicts.extend(probe(candidate, canonical))
        except Exception as exc:  # 프로브 장애가 승격을 무단 통과시키면 안 된다
            report.probed = False
            report.notes.append(f"손상 프로브 실패: {exc}")

    return report


def strict_gate(decision: PromotionDecision) -> bool:
    """프로브가 돌지 않았으면 승격시키지 않는 보수 모드.

    기본 판정은 "시험 불능"을 보류로 흘리지 않지만, 운영에서 정본을 지키고
    싶으면 이 게이트를 한 겹 더 씌운다.
    """
    return decision.promoted and decision.damage.probed


def apply(
    decision: PromotionDecision,
    node: Node,
    ontology: Ontology,
    *,
    on_dormant: Callable[[str], None] | None = None,
) -> Node:
    """판정을 온톨로지에 반영한다."""
    if decision.verdict is Verdict.PROMOTE:
        return ontology.promote(node.id, decision.reason)
    if decision.verdict is Verdict.DAMAGE_REJECT:
        result = ontology.make_dormant(node.id, decision.reason)
        if on_dormant is not None:
            on_dormant(node.id)   # 잠긴 포인트를 유동성 풀로 환원 (economy)
        return result
    node.observed_ticks += 1
    node.verdicts.append(f"[t{ontology.clock}] HOLD — {decision.reason}")
    return node
