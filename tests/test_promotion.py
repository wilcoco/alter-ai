"""석회화 판정 — 관문의 불변식 (v1.3).

핵심 셋:
1. **판정 기준은 novelty 가 아니라 손상이다** (danger model).
2. **판정권은 사람 증거와 현실에만 있다** — 기저 LLM 은 관문에 닿지 못한다.
3. **계류 반증은 차단하지 않고 동행한다** — 검증된 반증만이 손상이다.
"""

from __future__ import annotations

import inspect

import pytest

from app.core import promotion
from app.core.ontology import EdgeType, NodeStatus, NodeType, Ontology
from app.core.promotion import (
    Advisory,
    GateReason,
    PromotionPolicy,
    Verdict,
    apply,
    judge,
)
from app.core.verification import Direction, Measurement, VerificationRegistry

POLICY = PromotionPolicy(recognition_threshold=25.0, quarantine_ticks=1)


def _setup():
    onto = Ontology()
    onto.add_node(
        "known", NodeType.CLAIM, "이미 검증된 정본", status=NodeStatus.CANONICAL
    )
    candidate = onto.add_node("cand", NodeType.CONCLUSION, "새 후보")
    return onto, candidate


def _judge(onto, candidate, *, recognition=0.0, registry=None, challenged=False,
           policy=POLICY):
    return judge(
        candidate,
        onto,
        recognition=recognition,
        stake=recognition,
        registry=registry or VerificationRegistry(),
        policy=policy,
        challenged=challenged,
    )


# -- 판정권: LLM 은 관문에 닿을 수 없다 ---------------------------------------

def test_judge_has_no_channel_for_an_llm():
    """중력 5 재발 방지 — 인자에 프로브/LLM 통로가 존재하지 않는다."""
    params = set(inspect.signature(judge).parameters)
    assert not params & {"probe", "llm", "advisor", "advisories"}


def test_advisory_and_conflict_are_separate_types_with_no_bridge():
    """제보를 손상 신호로 바꾸는 변환은 코드베이스에 없어야 한다."""
    assert not issubclass(Advisory, promotion.Conflict)
    source = inspect.getsource(promotion)
    assert "Advisory" in source
    # damage_test 는 Advisory 를 읽지 않는다
    assert "Advisory" not in inspect.getsource(promotion.damage_test)


def test_core_promotion_never_imports_capture_layer():
    source = inspect.getsource(promotion)
    assert "app.capture" not in source


# -- 손상: 검증된 반증과 실측 악화만 -------------------------------------------

def test_novelty_alone_never_blocks_promotion():
    """낯섦은 감점 요인이 아니다 — 오히려 학습 가치다 (danger model)."""
    onto, candidate = _setup()
    candidate.observed_ticks = 1
    decision = _judge(onto, candidate, recognition=30.0)
    assert decision.verdict is Verdict.PROMOTE
    assert not decision.damage.damaged


def test_verified_refutation_is_damage_regardless_of_recognition():
    """인정이 아무리 쌓여도 자기를 손상시키면 통과 못한다."""
    onto, candidate = _setup()
    onto.add_edge("e1", "known", "cand", EdgeType.REFUTES)
    candidate.observed_ticks = 99
    decision = _judge(onto, candidate, recognition=10_000.0)
    assert decision.verdict is Verdict.DAMAGE_REJECT
    assert decision.reason_code is GateReason.DAMAGED_BY_CANONICAL_REFUTER


def test_pending_refutation_does_not_block_but_travels_along():
    """계류 반증은 차단하지 않는다 — ⚠ 로 동행할 뿐 (spec §5)."""
    onto, candidate = _setup()
    onto.add_node("chal", NodeType.CLAIM, "반박합니다")   # 폴립 상태
    onto.add_edge("e1", "chal", "cand", EdgeType.REFUTES)
    candidate.observed_ticks = 1

    assert onto.is_challenged("cand")
    decision = _judge(onto, candidate, recognition=30.0, challenged=True)
    assert decision.verdict is Verdict.PROMOTE      # 차단되지 않는다
    assert decision.challenged                      # 그러나 동행한다


def test_refutation_becomes_damage_only_after_it_is_promoted():
    """"반증했다"가 아니라 "반증이 검증됐다"가 손상이다."""
    onto, candidate = _setup()
    onto.add_node("chal", NodeType.CLAIM, "반박합니다")
    onto.add_edge("e1", "chal", "cand", EdgeType.REFUTES)
    candidate.observed_ticks = 1

    assert _judge(onto, candidate, recognition=30.0).verdict is Verdict.PROMOTE
    onto.promote("chal", "검증 통과")
    assert _judge(onto, candidate, recognition=30.0).verdict is Verdict.DAMAGE_REJECT


def test_measured_regression_is_damage():
    """실측이 악화를 보이면 손상 — 현실은 순환 밖의 신호다."""
    onto, candidate = _setup()
    registry = VerificationRegistry()
    registry.record(
        "cand", Measurement("수율", 90.0, 80.0, Direction.HIGHER_BETTER)
    )
    decision = _judge(onto, candidate, recognition=99.0, registry=registry)
    assert decision.verdict is Verdict.DAMAGE_REJECT
    assert decision.reason_code is GateReason.DAMAGED_BY_REALITY


def test_unchanged_measurement_is_not_a_refutation():
    """변화 없음·임계 미달은 "검증 안 됨"이지 반증이 아니다."""
    onto, candidate = _setup()
    registry = VerificationRegistry()
    registry.record("cand", Measurement("수율", 90.0, 90.0, Direction.HIGHER_BETTER))
    decision = _judge(onto, candidate, recognition=5.0, registry=registry)
    assert decision.verdict is Verdict.HOLD
    assert decision.reason_code is GateReason.RECOGNITION_BELOW_THRESHOLD


# -- 근거 사다리 ---------------------------------------------------------------

def test_reality_anchor_bypasses_the_recognition_threshold():
    """실측은 동의보다 강한 신호다 — 인정 0 이어도 승격."""
    onto, candidate = _setup()
    registry = VerificationRegistry()
    registry.record("cand", Measurement("수율", 80.0, 95.0, Direction.HIGHER_BETTER))
    decision = _judge(onto, candidate, recognition=0.0, registry=registry)
    assert decision.verdict is Verdict.PROMOTE
    assert decision.reason_code is GateReason.ANCHORED


def test_insufficient_recognition_holds_it_does_not_reject():
    """근거 부족은 기각이 아니라 보류 — 폴립층에 남는다."""
    onto, candidate = _setup()
    decision = _judge(onto, candidate, recognition=5.0)
    assert decision.verdict is Verdict.HOLD
    apply(decision, candidate, onto)
    assert candidate.status is NodeStatus.POLYP
    assert candidate.observed_ticks == 1      # 노출 생존 시간이 쌓인다


def test_quarantine_must_elapse_before_promotion():
    onto, candidate = _setup()
    policy = PromotionPolicy(recognition_threshold=25.0, quarantine_ticks=2)
    for expected in (1, 2):
        decision = _judge(onto, candidate, recognition=50.0, policy=policy)
        assert decision.reason_code is GateReason.QUARANTINE_PENDING
        apply(decision, candidate, onto)
        assert candidate.observed_ticks == expected
    assert _judge(onto, candidate, recognition=50.0, policy=policy).promoted


# -- 정본 재평가 ---------------------------------------------------------------

def test_canonical_is_re_reviewed_and_can_be_demoted():
    """정본도 예외가 아니다 — "느리게 사는 층" (spec §4)."""
    onto, _ = _setup()
    onto.add_node("chal", NodeType.CLAIM, "정설이 틀렸다", status=NodeStatus.CANONICAL)
    onto.add_edge("e1", "chal", "known", EdgeType.REFUTES)

    known = onto.nodes["known"]
    decision = _judge(onto, known, recognition=999.0)
    assert decision.verdict is Verdict.DAMAGE_REJECT
    apply(decision, known, onto)
    assert known.status is NodeStatus.DORMANT
    assert "known" in onto.nodes          # 잠들었을 뿐 사라지지 않았다


def test_damage_rejection_reclaims_points_without_burning():
    onto, candidate = _setup()
    onto.add_edge("e1", "known", "cand", EdgeType.REFUTES)
    decision = _judge(onto, candidate, recognition=50.0)
    reclaimed: list[str] = []
    apply(decision, candidate, onto, on_dormant=reclaimed.append)
    assert candidate.status is NodeStatus.DORMANT
    assert reclaimed == ["cand"]


def test_every_decision_carries_a_reason_code():
    """거절 이유가 코드로 나가야 UI 가 "무엇을 더 하면 되는지" 안내할 수 있다."""
    onto, candidate = _setup()
    for recognition in (0.0, 50.0):
        decision = _judge(onto, candidate, recognition=recognition)
        assert isinstance(decision.reason_code, GateReason)
        assert decision.reason
