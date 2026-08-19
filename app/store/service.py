"""한 바퀴 오케스트레이션 — MVP 의 본체.

    사용자 질문
      → (정본 주입) 기저 LLM 답변
      → P→I→C 구조화                     [포획]
      → 사용자 승인/증분
      → 스테이킹 / 외부 실측 닻            [검증·가격]
      → 승격 판정 (손상 시험)              [석회화 판정 기관]
      → 온톨로지 정본에 굳음               [승격 (a)경로]
      → 다음 질문에서 참조                 [환류 — 바퀴가 닫힌다]

각 메서드는 DB 세션 하나를 열고, 도메인 코어 객체를 조립하고, 판정하고,
결과를 다시 쓴다. 코어는 저장소를 끝까지 모른다.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select as sa_select
from sqlalchemy.orm import Session

from app import injection
from app.capture import damage as damage_mod
from app.capture import pic
from app.capture.llm import Answer, get_llm
from app.config import settings
from app.core.economy import Economy, InsufficientPoints, Ledger
from app.core.ontology import (
    Edge,
    EdgeType,
    Node,
    NodeStatus,
    NodeType,
    Ontology,
    OntologyError,
)
from app.core.promotion import PromotionPolicy, Verdict, apply as apply_decision, judge
from app.core.verification import Direction, Measurement, VerificationRegistry
from app.store import db

log = logging.getLogger(__name__)


class ServiceError(Exception):
    """사용자에게 그대로 보여줄 수 있는 규칙 위반."""


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# 코어 객체 조립/해체
# ---------------------------------------------------------------------------

def _row_to_node(row: db.NodeRow) -> Node:
    return Node(
        id=row.id,
        type=NodeType(row.type),
        title=row.title,
        content=row.content,
        author=row.author,
        turn_id=row.turn_id,
        status=NodeStatus(row.status),
        value_add=row.value_add,
        created_at=row.seq,
        observed_ticks=row.observed_ticks,
        verdicts=[v for v in (row.verdicts or "").split("\n") if v],
    )


def build_ontology(session: Session) -> Ontology:
    """DB → 도메인 온톨로지 (링크 순서까지 재생해 허브/권위를 복원)."""
    ontology = Ontology(large_stake_threshold=settings.large_stake_threshold)
    rows = session.scalars(sa_select(db.NodeRow).order_by(db.NodeRow.seq)).all()
    for row in rows:
        ontology.nodes[row.id] = _row_to_node(row)
    ontology._clock = max((r.seq for r in rows), default=0)

    for edge in session.scalars(sa_select(db.EdgeRow)).all():
        if edge.source_id in ontology.nodes and edge.target_id in ontology.nodes:
            ontology.edges[edge.id] = Edge(
                id=edge.id,
                source_id=edge.source_id,
                target_id=edge.target_id,
                type=EdgeType(edge.type),
            )

    # 링크는 반드시 **기록된 순서대로** 다시 먹인다 — 순서가 안목의 정의다.
    links = session.scalars(
        sa_select(db.LinkRow).order_by(db.LinkRow.id)
    ).all()
    for link in links:
        if link.node_id in ontology.nodes:
            ontology.scoring.link(link.evaluator, link.node_id, weight=link.weight)
    return ontology


def build_ledger(session: Session) -> Ledger:
    ledger = Ledger()
    for account in session.scalars(sa_select(db.Account)).all():
        ledger.available[account.id] = account.balance
    for stake in session.scalars(sa_select(db.StakeRow)).all():
        if not stake.reclaimed:
            ledger.staked[stake.node_id][stake.account] += stake.amount
    pool = session.get(db.Meta, "liquidity_pool")
    ledger.liquidity_pool = pool.value if pool is not None else 0.0
    return ledger


def build_registry(session: Session) -> VerificationRegistry:
    registry = VerificationRegistry()
    for row in session.scalars(sa_select(db.MeasurementRow)).all():
        registry.record(
            row.node_id,
            Measurement(
                metric=row.metric,
                baseline=row.baseline,
                observed=row.observed,
                direction=Direction(row.direction),
                unit=row.unit,
                min_rel_improvement=row.min_rel_improvement,
            ),
        )
    return registry


def _flush_balances(session: Session, ledger: Ledger) -> None:
    """원장의 잔액 + 유동성 풀을 DB 에 되쓴다.

    유동성 풀은 잠복 노드에서 회수된(소각되지 않은) 포인트다. 부활 때 원
    스테이커에게 복원되어야 하므로 요청 사이에도 살아 있어야 한다.
    """
    existing = {a.id: a for a in session.scalars(sa_select(db.Account)).all()}
    for account_id, balance in ledger.available.items():
        row = existing.get(account_id)
        if row is None:
            session.add(db.Account(id=account_id, balance=balance))
        else:
            row.balance = balance
    pool = session.get(db.Meta, "liquidity_pool")
    if pool is None:
        session.add(db.Meta(key="liquidity_pool", value=ledger.liquidity_pool))
    else:
        pool.value = ledger.liquidity_pool


def _next_seq(session: Session) -> int:
    row = session.get(db.Meta, "node_seq")
    if row is None:
        row = db.Meta(key="node_seq", value=0.0)
        session.add(row)
    row.value += 1
    return int(row.value)


def ensure_account(session: Session, account_id: str) -> db.Account:
    """신규 참여자에게 UBI 를 발행한다 (자본 장벽 완화 — 가난한 천재 문제)."""
    row = session.get(db.Account, account_id)
    if row is None:
        row = db.Account(id=account_id, balance=settings.ubi_grant)
        session.add(row)
        session.flush()
    return row


def _policy() -> PromotionPolicy:
    return PromotionPolicy(
        stake_threshold=settings.promotion_stake_threshold,
        quarantine_ticks=settings.quarantine_ticks,
    )


# ---------------------------------------------------------------------------
# 1. 질문 → 답 → 포획
# ---------------------------------------------------------------------------

@dataclass
class AskResult:
    turn_id: str
    question: str
    answer: Answer
    node_ids: list[str]
    summary: str
    fallback: bool


def ask(session: Session, question: str, author: str) -> AskResult:
    """한 바퀴의 입구. 주입 → 답변 → P→I→C 포획까지 한 번에."""
    question = question.strip()
    if not question:
        raise ServiceError("질문이 비어 있다")
    ensure_account(session, author)

    ontology = build_ontology(session)
    canonical = ontology.canonical()
    chosen = injection.select(
        question,
        canonical,
        authority={n.id: ontology.authority_of(n.id) for n in canonical},
        top_k=settings.injection_top_k,
    )
    context = injection.render(chosen)

    llm = get_llm()
    try:
        text = llm.answer(question, context)
    except Exception as exc:
        log.exception("기저 LLM 호출 실패")
        raise ServiceError(f"기저 LLM 호출 실패: {exc}") from exc

    answer = Answer(
        text=text,
        model=llm.name,
        injected_ids=[n.id for n in chosen],
        stubbed=llm.name == "stub",
    )

    turn_id = _uid("turn")
    session.add(
        db.Turn(
            id=turn_id,
            author=author,
            question=question,
            base_answer=answer.text,
            injected_ids=",".join(answer.injected_ids),
            model=answer.model,
            stubbed=answer.stubbed,
        )
    )
    session.flush()

    graph = pic.capture(llm, question, answer.text)
    node_ids = _persist_graph(session, graph, turn_id=turn_id, author=author)

    # 문답 자체도 노드다 (qa) — 귀속의 뿌리이자 H2A2H2 스키마의 노드 타입.
    qa_id = _add_node(
        session,
        type=NodeType.QA,
        title=question[:200],
        content=answer.text,
        author=author,
        turn_id=turn_id,
    )
    node_ids.insert(0, qa_id)

    session.commit()
    return AskResult(
        turn_id=turn_id,
        question=question,
        answer=answer,
        node_ids=node_ids,
        summary=graph.summary,
        fallback=graph.fallback,
    )


def _add_node(
    session: Session,
    *,
    type: NodeType,
    title: str,
    content: str,
    author: str,
    turn_id: str | None,
    value_add: bool = True,
) -> str:
    node_id = _uid("n")
    session.add(
        db.NodeRow(
            id=node_id,
            type=type.value,
            title=title,
            content=content,
            author=author,
            turn_id=turn_id,
            status=NodeStatus.POLYP.value,
            value_add=value_add,
            seq=_next_seq(session),
        )
    )
    session.flush()
    return node_id


def _persist_graph(
    session: Session, graph: pic.PICGraph, *, turn_id: str, author: str
) -> list[str]:
    ref_to_id: dict[str, str] = {}
    for draft in graph.nodes:
        node_id = _add_node(
            session,
            type=draft.type,
            title=draft.title,
            content=draft.content,
            author=author,
            turn_id=turn_id,
        )
        ref_to_id[draft.ref] = node_id
    for draft_edge in graph.edges:
        source = ref_to_id.get(draft_edge.source_ref)
        target = ref_to_id.get(draft_edge.target_ref)
        if source and target:
            session.add(
                db.EdgeRow(
                    id=_uid("e"),
                    source_id=source,
                    target_id=target,
                    type=draft_edge.type.value,
                )
            )
    session.flush()
    return list(ref_to_id.values())


# ---------------------------------------------------------------------------
# 2. 사용자 승인 / 증분
# ---------------------------------------------------------------------------

def respond(
    session: Session, turn_id: str, *, accept: bool, increment: str, author: str
) -> str | None:
    """기저 답을 그대로 채택하거나, 가치를 증분한다 (spec §3 — 문답의 소유자는 사용자).

    증분은 새 ``claim`` 폴립이 된다. 증분 여부는 라우팅(기저 답 채택 vs 상부
    개입)의 원시 신호이기도 하다 — 지금은 기록만 하고 라우팅 함수는 미구현이다.
    """
    turn = session.get(db.Turn, turn_id)
    if turn is None:
        raise ServiceError(f"unknown turn {turn_id!r}")
    ensure_account(session, author)
    turn.accepted = accept
    turn.increment = increment.strip()

    node_id = None
    if increment.strip():
        node_id = _add_node(
            session,
            type=NodeType.CLAIM,
            title=increment.strip().split("\n")[0][:200],
            content=increment.strip(),
            author=author,
            turn_id=turn_id,
        )
        # 증분은 문답 노드에서 이어진 추론이다.
        qa_row = session.scalars(
            sa_select(db.NodeRow).where(
                db.NodeRow.turn_id == turn_id, db.NodeRow.type == NodeType.QA.value
            )
        ).first()
        if qa_row is not None:
            session.add(
                db.EdgeRow(
                    id=_uid("e"),
                    source_id=qa_row.id,
                    target_id=node_id,
                    type=EdgeType.INFERS.value,
                )
            )
    session.commit()
    return node_id


# ---------------------------------------------------------------------------
# 3. 스테이킹 / 실측 닻
# ---------------------------------------------------------------------------

def stake(session: Session, node_id: str, account: str, amount: float) -> dict[str, Any]:
    """확신을 건다. 배당은 검증된 가지에서만 나간다."""
    node_row = session.get(db.NodeRow, node_id)
    if node_row is None:
        raise ServiceError(f"unknown node {node_id!r}")
    if amount <= 0:
        raise ServiceError("스테이크는 양수여야 한다")
    ensure_account(session, account)

    ontology = build_ontology(session)
    ledger = build_ledger(session)
    node = ontology.require(node_id)
    try:
        ontology.check_stake_rule(amount, node.value_add)
        ledger.stake(account, node_id, amount)
    except (OntologyError, InsufficientPoints) as exc:
        raise ServiceError(str(exc)) from exc

    session.add(db.StakeRow(node_id=node_id, account=account, amount=amount))
    # 스테이킹은 곧 링크다 — 순서가 기록되고 안목이 계산된다.
    session.add(db.LinkRow(node_id=node_id, evaluator=account, weight=1.0))

    economy = Economy(ledger=ledger)
    payouts = economy.distribute_dividend(
        fresh_stake=amount,
        staker=account,
        ancestors=ontology.ancestors(node_id),
        hub_of=ontology.hub_of,
        is_verified=build_registry(session).is_verified,
    )
    _flush_balances(session, ledger)
    session.commit()
    return {
        "node_id": node_id,
        "staked": amount,
        "total_staked": total_stake(session, node_id),
        "dividends": payouts,
        "balance": ledger.balance(account),
    }


def total_stake(session: Session, node_id: str) -> float:
    rows = session.scalars(
        sa_select(db.StakeRow).where(
            db.StakeRow.node_id == node_id, db.StakeRow.reclaimed.is_(False)
        )
    ).all()
    return sum(r.amount for r in rows)


def measure(
    session: Session,
    node_id: str,
    *,
    metric: str,
    baseline: float,
    observed: float,
    direction: str = "higher_better",
    unit: str = "",
    min_rel_improvement: float = 0.0,
    reporter: str = "",
) -> dict[str, Any]:
    """외부 현실 닻을 박는다 — 동의의 순환 밖에 있는 유일한 신호."""
    if session.get(db.NodeRow, node_id) is None:
        raise ServiceError(f"unknown node {node_id!r}")
    try:
        direction_enum = Direction(direction)
    except ValueError as exc:
        raise ServiceError(f"direction 은 higher_better/lower_better: {exc}") from exc

    m = Measurement(
        metric=metric,
        baseline=baseline,
        observed=observed,
        direction=direction_enum,
        unit=unit,
        min_rel_improvement=min_rel_improvement,
    )
    session.add(
        db.MeasurementRow(
            node_id=node_id,
            metric=metric,
            baseline=baseline,
            observed=observed,
            direction=direction_enum.value,
            unit=unit,
            min_rel_improvement=min_rel_improvement,
            reporter=reporter,
        )
    )
    session.commit()
    return {
        "node_id": node_id,
        "passes": m.passes,
        "relative_improvement": m.relative_improvement,
    }


# ---------------------------------------------------------------------------
# 4. 승격 판정 (석회화)
# ---------------------------------------------------------------------------

def _write_node(session: Session, node: Node) -> None:
    row = session.get(db.NodeRow, node.id)
    if row is None:
        return
    row.status = node.status.value
    row.observed_ticks = node.observed_ticks
    row.verdicts = "\n".join(node.verdicts)


def promote(
    session: Session, node_id: str, *, use_probe: bool = True
) -> dict[str, Any]:
    """한 후보에 대한 석회화 판정 → 반영 → 기록."""
    if session.get(db.NodeRow, node_id) is None:
        raise ServiceError(f"unknown node {node_id!r}")

    ontology = build_ontology(session)
    ledger = build_ledger(session)
    registry = build_registry(session)
    node = ontology.require(node_id)

    probe = None
    if use_probe:
        llm = get_llm()
        if llm.name != "stub":
            probe = damage_mod.make_probe(llm)

    decision = judge(
        node,
        ontology,
        stake=ledger.total_staked(node_id),
        registry=registry,
        policy=_policy(),
        probe=probe,
    )

    economy = Economy(ledger=ledger)
    reclaimed: list[str] = []

    def on_dormant(nid: str) -> None:
        # 잠긴 포인트는 소각하지 않고 유동성 풀로 환원한다 — 가지는 죽지 않는다.
        economy.reclaim_dormant(nid)
        reclaimed.append(nid)

    ontology.tick()
    apply_decision(decision, node, ontology, on_dormant=on_dormant)
    _write_node(session, node)

    if reclaimed:
        for row in session.scalars(
            sa_select(db.StakeRow).where(
                db.StakeRow.node_id == node_id, db.StakeRow.reclaimed.is_(False)
            )
        ).all():
            row.reclaimed = True

    session.add(
        db.PromotionRow(
            node_id=node_id,
            verdict=decision.verdict.value,
            reason=decision.reason,
            damage_summary=decision.damage.summary(),
            probed=decision.damage.probed,
            stake=decision.stake,
            anchored=decision.anchored,
        )
    )
    _flush_balances(session, ledger)
    session.commit()

    return {
        "node_id": node_id,
        "verdict": decision.verdict.value,
        "reason": decision.reason,
        "damage": {
            "damaged": decision.damage.damaged,
            "probed": decision.damage.probed,
            "summary": decision.damage.summary(),
            "notes": decision.damage.notes,
            "conflicts": [
                {
                    "canonical_id": c.canonical_id,
                    "kind": c.kind,
                    "detail": c.detail,
                    "confidence": c.confidence,
                }
                for c in decision.damage.conflicts
            ],
        },
        "stake": decision.stake,
        "anchored": decision.anchored,
        "status": node.status.value,
    }


def promote_all(session: Session, *, use_probe: bool = True) -> list[dict[str, Any]]:
    """폴립층 전체를 한 번 심사한다 (관문 틱)."""
    polyp_ids = [
        row.id
        for row in session.scalars(
            sa_select(db.NodeRow)
            .where(db.NodeRow.status == NodeStatus.POLYP.value)
            .order_by(db.NodeRow.seq)
        ).all()
    ]
    return [promote(session, node_id, use_probe=use_probe) for node_id in polyp_ids]


def revive(session: Session, node_id: str, finder: str) -> dict[str, Any]:
    """잠복 가지를 되살린다. 원 스테이커 복원 + 발견자 보너스."""
    ontology = build_ontology(session)
    ledger = build_ledger(session)
    ensure_account(session, finder)

    node = ontology.nodes.get(node_id)
    if node is None:
        raise ServiceError(f"unknown node {node_id!r}")
    if node.status is not NodeStatus.DORMANT:
        raise ServiceError(f"{node_id} 는 잠복 상태가 아니다 (현재 {node.status.value})")

    economy = Economy(ledger=ledger)
    reclaimed_rows = session.scalars(
        sa_select(db.StakeRow).where(
            db.StakeRow.node_id == node_id, db.StakeRow.reclaimed.is_(True)
        )
    ).all()
    restored_info: dict[str, float] = {}
    if reclaimed_rows:
        stakes: dict[str, float] = {}
        for row in reclaimed_rows:
            stakes[row.account] = stakes.get(row.account, 0.0) + row.amount
        # 회수 기록을 economy 에 복원해 넣어야 restore 가 가능하다. 유동성
        # 풀 자체는 build_ledger 가 이미 DB 에서 복원했으므로 더하지 않는다.
        economy._reclaimed[node_id] = stakes
        restored_info = economy.restore_on_revival(node_id, finder)
        for row in reclaimed_rows:
            row.reclaimed = False

    ontology.tick()
    ontology.revive(node_id, finder)
    _write_node(session, ontology.nodes[node_id])
    _flush_balances(session, ledger)
    session.commit()
    return {
        "node_id": node_id,
        "status": NodeStatus.POLYP.value,
        "restored": restored_info,
    }


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------

def node_view(session: Session, ontology: Ontology, node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type.value,
        "title": node.title,
        "content": node.content,
        "author": node.author,
        "status": node.status.value,
        "turn_id": node.turn_id,
        "value_add": node.value_add,
        "observed_ticks": node.observed_ticks,
        "stake": total_stake(session, node.id),
        "authority": round(ontology.authority_of(node.id), 3),
        "verified": build_registry(session).is_verified(node.id),
        "verdicts": node.verdicts,
    }


def snapshot(session: Session) -> dict[str, Any]:
    """3-패널 UI 한 화면분."""
    ontology = build_ontology(session)
    registry = build_registry(session)
    ledger = build_ledger(session)

    def view(node: Node) -> dict[str, Any]:
        return {
            "id": node.id,
            "type": node.type.value,
            "title": node.title,
            "content": node.content,
            "author": node.author,
            "status": node.status.value,
            "observed_ticks": node.observed_ticks,
            "stake": round(ledger.total_staked(node.id), 2),
            "authority": round(ontology.authority_of(node.id), 3),
            "verified": registry.is_verified(node.id),
            "verdicts": node.verdicts[-3:],
        }

    turns = session.scalars(
        sa_select(db.Turn).order_by(db.Turn.created_at.desc()).limit(20)
    ).all()
    accounts = session.scalars(
        sa_select(db.Account).order_by(db.Account.balance.desc()).limit(20)
    ).all()

    return {
        "canonical": [view(n) for n in sorted(
            ontology.canonical(), key=lambda n: -ontology.authority_of(n.id)
        )],
        "polyps": [view(n) for n in sorted(ontology.polyps(), key=lambda n: -n.created_at)],
        "dormant": [view(n) for n in ontology.dormant()],
        "turns": [
            {
                "id": t.id,
                "author": t.author,
                "question": t.question,
                "answer": t.base_answer,
                "injected_ids": [i for i in (t.injected_ids or "").split(",") if i],
                "model": t.model,
                "stubbed": t.stubbed,
                "accepted": t.accepted,
                "increment": t.increment,
            }
            for t in turns
        ],
        "accounts": [
            {
                "id": a.id,
                "balance": round(a.balance, 2),
                "hub": round(ontology.hub_of(a.id), 3),
            }
            for a in accounts
        ],
        "stats": {
            "canonical": len(ontology.canonical()),
            "polyp": len(ontology.polyps()),
            "dormant": len(ontology.dormant()),
            "turns": session.scalar(sa_select(func.count()).select_from(db.Turn)) or 0,
            "llm": get_llm().name,
            "liquidity_pool": round(ledger.liquidity_pool, 2),
        },
    }
