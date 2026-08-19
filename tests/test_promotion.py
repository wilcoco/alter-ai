"""석회화 판정 — 관문의 불변식.

핵심: **판정 기준은 novelty 가 아니라 손상이다.** 낯설다는 이유로 기각되면
안 되고, 손상이면 스테이크가 아무리 많아도 통과하면 안 된다.
"""

from __future__ import annotations

from app.core.ontology import EdgeType, NodeStatus, NodeType, Ontology
from app.core.promotion import (
    Conflict,
    PromotionPolicy,
    Verdict,
    apply,
    judge,
    strict_gate,
)
from app.core.verification import Direction, Measurement, VerificationRegistry

POLICY = PromotionPolicy(stake_threshold=25.0, quarantine_ticks=1)


def _setup():
    onto = Ontology()
    onto.add_node("known", NodeType.CLAIM, "이미 검증된 정본", status=NodeStatus.CANONICAL)
    candidate = onto.add_node("cand", NodeType.CONCLUSION, "새 후보")
    return onto, candidate


def test_novelty_alone_never_blocks_promotion():
    """낯섦은 감점 요인이 아니다 — 오히려 학습 가치다 (danger model)."""
    onto, candidate = _setup()
    candidate.observed_ticks = 1
    decision = judge(
        candidate, onto, stake=30.0,
        registry=VerificationRegistry(), policy=POLICY,
        probe=lambda c, canon: [],      # 모순 없음
    )
    assert decision.verdict is Verdict.PROMOTE
    assert not decision.damage.damaged


def test_damage_rejects_regardless_of_stake():
    """스테이크가 아무리 많아도 자기를 손상시키면 통과 못한다."""
    onto, candidate = _setup()
    candidate.observed_ticks = 99
    decision = judge(
        candidate, onto, stake=10_000.0,
        registry=VerificationRegistry(), policy=POLICY,
        probe=lambda c, canon: [
            Conflict("known", "semantic", "정본과 동시에 참일 수 없다")
        ],
    )
    assert decision.verdict is Verdict.DAMAGE_REJECT


def test_refutes_edge_from_canonical_is_structural_damage():
    """프로브가 없어도 정본의 refutes 엣지는 손상으로 잡힌다."""
    onto, candidate = _setup()
    onto.add_edge("e1", "known", "cand", EdgeType.REFUTES)
    candidate.observed_ticks = 5
    decision = judge(
        candidate, onto, stake=100.0,
        registry=VerificationRegistry(), policy=POLICY,
    )
    assert decision.verdict is Verdict.DAMAGE_REJECT
    assert decision.damage.conflicts[0].kind == "refutes_edge"


def test_reality_anchor_bypasses_the_stake_threshold():
    """실측은 동의보다 강한 신호다 — 스테이크 0 이어도 승격."""
    onto, candidate = _setup()
    registry = VerificationRegistry()
    registry.record("cand", Measurement("수율", 80.0, 95.0, Direction.HIGHER_BETTER))
    decision = judge(
        candidate, onto, stake=0.0,
        registry=registry, policy=POLICY, probe=lambda c, canon: [],
    )
    assert decision.verdict is Verdict.PROMOTE
    assert decision.anchored


def test_insufficient_stake_holds_it_does_not_reject():
    """근거 부족은 기각이 아니라 보류 — 폴립층에 남는다."""
    onto, candidate = _setup()
    decision = judge(
        candidate, onto, stake=5.0,
        registry=VerificationRegistry(), policy=POLICY, probe=lambda c, canon: [],
    )
    assert decision.verdict is Verdict.HOLD
    apply(decision, candidate, onto)
    assert candidate.status is NodeStatus.POLYP
    assert candidate.observed_ticks == 1      # 시간이 검증자 — 틱이 쌓인다


def test_quarantine_must_elapse_before_promotion():
    onto, candidate = _setup()
    policy = PromotionPolicy(stake_threshold=25.0, quarantine_ticks=2)
    for expected_ticks in (1, 2):
        decision = judge(
            candidate, onto, stake=50.0,
            registry=VerificationRegistry(), policy=policy, probe=lambda c, k: [],
        )
        assert decision.verdict is Verdict.HOLD
        apply(decision, candidate, onto)
        assert candidate.observed_ticks == expected_ticks

    decision = judge(
        candidate, onto, stake=50.0,
        registry=VerificationRegistry(), policy=policy, probe=lambda c, k: [],
    )
    assert decision.verdict is Verdict.PROMOTE


def test_probe_failure_is_not_a_pass():
    """시험 불능은 통과가 아니다 — strict_gate 가 막는다."""
    onto, candidate = _setup()
    candidate.observed_ticks = 1

    def broken_probe(candidate, canonical):
        raise RuntimeError("LLM 다운")

    decision = judge(
        candidate, onto, stake=50.0,
        registry=VerificationRegistry(), policy=POLICY, probe=broken_probe,
    )
    assert decision.verdict is Verdict.PROMOTE      # 기본 판정은 통과시키지만
    assert not decision.damage.probed
    assert not strict_gate(decision)                # 보수 게이트는 막는다


def test_damage_rejection_makes_it_dormant_not_deleted():
    onto, candidate = _setup()
    decision = judge(
        candidate, onto, stake=50.0,
        registry=VerificationRegistry(), policy=POLICY,
        probe=lambda c, k: [Conflict("known", "semantic", "모순")],
    )
    reclaimed: list[str] = []
    apply(decision, candidate, onto, on_dormant=reclaimed.append)
    assert candidate.status is NodeStatus.DORMANT
    assert "cand" in onto.nodes          # 잠들었을 뿐 사라지지 않았다
    assert reclaimed == ["cand"]         # 잠긴 포인트는 회수(소각 아님)
