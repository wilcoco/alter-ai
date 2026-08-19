"""도메인 코어 불변식 — 프레임워크 없이, 순수 파이썬으로."""

from __future__ import annotations

import pytest

from app.core.economy import Economy, InsufficientPoints, Ledger
from app.core.ontology import (
    EdgeType,
    NodeStatus,
    NodeType,
    Ontology,
    OntologyError,
)
from app.core.scoring import ScoreEngine
from app.core.verification import Direction, Measurement, VerificationRegistry


# -- scoring: 통화는 인기가 아니라 안목 -------------------------------------

def test_early_discoverer_out_earns_late_joiner():
    engine = ScoreEngine()
    for evaluator in ["first", "second", "third", "fourth", "fifth"]:
        engine.link(evaluator, "node")
    assert engine.hub_of("first") > engine.hub_of("second") > engine.hub_of("third")
    # 마지막에 올라탄 사람은 아직 아무에게도 검증받지 못했다.
    assert engine.hub_of("fifth") == 0.0


def test_high_hub_endorsement_confers_more_authority():
    engine = ScoreEngine()
    for e in ["expert", "b", "c", "d"]:
        engine.link(e, "old")          # expert 가 안목을 쌓는다
    plain, expert_backed = ScoreEngine(), engine
    plain.link("nobody", "x")
    expert_backed.link("expert", "y")
    assert expert_backed.authority_of("y") > plain.authority_of("x")


# -- ontology: 포인트 크기 = 기여 크기, 삭제 없음 ---------------------------

def test_large_stake_without_contribution_is_rejected():
    onto = Ontology(large_stake_threshold=25.0)
    onto.check_stake_rule(10.0, value_add=False)          # 작은 동의는 허용
    with pytest.raises(OntologyError, match="기여"):
        onto.check_stake_rule(100.0, value_add=False)     # 돈으로 가중치 못 산다


def test_rejected_node_goes_dormant_not_deleted_and_can_revive():
    onto = Ontology()
    onto.add_node("n1", NodeType.CLAIM, "갈릴레오의 주장")
    onto.make_dormant("n1", "당대 정본과 충돌")
    assert onto.nodes["n1"].status is NodeStatus.DORMANT
    assert "n1" in onto.nodes                              # 지워지지 않았다

    onto.revive("n1", finder="kepler")
    assert onto.nodes["n1"].status is NodeStatus.POLYP
    assert any("REVIVED" in v for v in onto.nodes["n1"].verdicts)


def test_ancestors_follow_the_pic_chain():
    onto = Ontology()
    onto.add_node("p", NodeType.PREMISE, "전제", author="alice")
    onto.add_node("i", NodeType.INFERENCE, "추론", author="bob")
    onto.add_node("c", NodeType.CONCLUSION, "결론", author="carol")
    onto.add_edge("e1", "p", "i", EdgeType.INFERS)
    onto.add_edge("e2", "i", "c", EdgeType.INFERS)

    chain = onto.ancestors("c")
    assert [node_id for node_id, _a, _v in chain] == ["i", "p"]   # 가까운 순


# -- verification: 실측만이 동의의 순환 밖에 있다 ---------------------------

def test_measurement_requires_real_improvement():
    improved = Measurement("불량률", 8.0, 2.0, Direction.LOWER_BETTER,
                           min_rel_improvement=0.2)
    unchanged = Measurement("불량률", 8.0, 8.0, Direction.LOWER_BETTER)
    worse = Measurement("수율", 90.0, 85.0, Direction.HIGHER_BETTER)
    assert improved.passes
    assert not unchanged.passes      # 변화 없음은 검증이 아니다
    assert not worse.passes          # 악화는 당연히 아니다


def test_dividend_is_gated_on_an_externally_verified_branch():
    """검증 닻 없는 가지에서는 배당이 나가지 않는다 — 폰지와의 구조적 경계선."""
    registry = VerificationRegistry()
    economy = Economy(ledger=Ledger())
    ancestors = [("n2", "bob", True), ("n1", "alice", True)]

    unverified = economy.distribute_dividend(
        100.0, "carol", ancestors, is_verified=registry.is_verified
    )
    assert unverified == {}

    registry.record("n1", Measurement("수율", 80.0, 95.0, Direction.HIGHER_BETTER))
    verified = economy.distribute_dividend(
        100.0, "carol", ancestors, is_verified=registry.is_verified
    )
    assert set(verified) == {"alice", "bob"}
    assert verified["alice"] > verified["bob"]   # 더 이른 기여자가 더 받는다


def test_no_value_add_node_is_bypassed_not_paid():
    """단순 동의 노드는 배당 통로에서 우회된다 — 지대와 기여의 경계."""
    economy = Economy(ledger=Ledger())
    payouts = economy.distribute_dividend(
        100.0,
        "staker",
        [("n2", "freerider", False), ("n1", "contributor", True)],
    )
    assert "freerider" not in payouts
    assert "contributor" in payouts


def test_cannot_stake_more_than_you_have():
    ledger = Ledger()
    ledger.mint("alice", 10.0)
    with pytest.raises(InsufficientPoints):
        ledger.stake("alice", "n1", 50.0)


def test_dormant_points_are_reclaimed_not_burned_and_restore_on_revival():
    """가지는 죽지 않게, 유동성은 마르지 않게 (critique §1.2)."""
    economy = Economy(ledger=Ledger())
    economy.ledger.mint("alice", 100.0)
    economy.ledger.stake("alice", "n1", 60.0)

    reclaimed = economy.reclaim_dormant("n1")
    assert reclaimed == 60.0
    assert economy.ledger.liquidity_pool == 60.0
    assert economy.ledger.burned == 0.0            # 소각이 아니다

    result = economy.restore_on_revival("n1", finder="bob")
    assert result["restored"] == 60.0
    assert economy.ledger.stake_on("n1")["alice"] == 60.0
    assert economy.ledger.balance("bob") > 0       # 발견자 보너스
