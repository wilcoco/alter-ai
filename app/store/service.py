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
from dataclasses import dataclass, field
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
from app.core.promotion import (
    Advisory,
    GateReason,
    PromotionPolicy,
    Verdict,
    apply as apply_decision,
    judge,
)
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
        recognition_threshold=settings.recognition_threshold,
        quarantine_ticks=settings.quarantine_ticks,
    )


def recognition_of(ontology: Ontology, node_id: str) -> float:
    """관문 지표 — 누적 인정 지수 (순서·허브 가중 권위).

    특허 10-0913256 의 기관이 여기서 관문에 연결된다. 원시 스테이크 합이 아닌
    이유: 밴드왜건 내성(늦은 편승은 거의 못 번다)과 안목 가중(입증된 평가자의
    인정이 더 무겁다)이 산식에 내장되어 있기 때문이다. 스테이크는 경제적
    잠금으로 병존하되 관문 지표는 권위다.

    ⚠ 현 `scoring.py` 는 특허의 제3 변주(순위 감쇠 `weight/j`)이며 청구항 8의
    평균 정규화가 빠져 있다. 산식이 관문 지표가 된 이상 이것은 공개 규약이다 —
    차이는 `app/core/scoring.py` 상단과 docs/promotion-gate-spec.md §3.5 에 기록.
    """
    return ontology.authority_of(node_id)


# ---------------------------------------------------------------------------
# 0. 검색 선점 — 불변 코어 루프의 2단계
# ---------------------------------------------------------------------------

def search(session: Session, query: str, *, viewer: str = "") -> dict[str, Any]:
    """**LLM 을 부르기 전에** 기존 문답을 보여준다 (spec §0 STEP 2).

    주입과 다르다: 주입은 LLM 을 *더 잘* 부르는 것이고, 이것은 LLM 을 *안* 부르는
    것이다. 히트해서 여기서 끝나면 그것이 성공이지 이탈이 아니다.

    그리고 이 화면이 곧 심사대다 — 결과에 폴립도 포함되고, 각 항목에 판단에
    필요한 상태(검증/검토중/⚠반박)가 동봉된다 (spec §3 검색-노출-석회화 회로).
    """
    query = query.strip()
    if not query:
        return {"query": "", "results": [], "explored": []}

    ontology = build_ontology(session)
    registry = build_registry(session)
    ledger = build_ledger(session)

    # 문답(qa) 노드도 검색 대상이다 — "누가 무엇을 묻고 무엇을 얻었는가"가
    # 사용자에게 가장 자연스러운 재사용 단위다. 잠복만 제외한다
    # (잠복은 "밀려난 답" 표면이 따로 맡는다).
    visible = [
        n for n in ontology.nodes.values() if n.status is not NodeStatus.DORMANT
    ]
    authority = {n.id: ontology.authority_of(n.id) for n in visible}
    hits, explored = injection.search(
        query,
        visible,
        authority=authority,
        top_k=settings.search_top_k,
        explore_quota=settings.explore_quota,
    )

    results = []
    for node in hits:
        pending = ontology.pending_refuters_of(node.id)
        results.append(
            {
                "id": node.id,
                "type": node.type.value,
                "title": node.title,
                "content": node.content[:600],
                "author": node.author,
                "status": node.status.value,
                "verified": registry.is_verified(node.id),
                "recognition": round(authority.get(node.id, 0.0), 2),
                "stake": round(ledger.total_staked(node.id), 2),
                "challenged": bool(pending),
                "challenges": [
                    {
                        "id": r.id,
                        "title": r.title,
                        "author": r.author,
                        "stake": round(ledger.total_staked(r.id), 2),
                    }
                    for r in pending
                ],
                "explore_slot": node.id in explored,
                "measurements": _measurement_view(session, node.id),
            }
        )
    return {"query": query, "results": results, "explored": sorted(explored)}


def _measurement_view(session: Session, node_id: str) -> list[dict[str, Any]]:
    rows = session.scalars(
        sa_select(db.MeasurementRow).where(db.MeasurementRow.node_id == node_id)
    ).all()
    out = []
    for row in rows:
        m = Measurement(
            metric=row.metric,
            baseline=row.baseline,
            observed=row.observed,
            direction=Direction(row.direction),
            unit=row.unit,
            min_rel_improvement=row.min_rel_improvement,
        )
        out.append(
            {
                "metric": row.metric,
                "baseline": row.baseline,
                "observed": row.observed,
                "unit": row.unit,
                "passes": m.passes,
                "improvement": round(m.relative_improvement, 4),
            }
        )
    return out


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
    #: 주입된 정본의 {id, title, author, challenged} — 기여자가 자기 지식이
    #: 쓰였다는 것을 볼 수 있어야 기여할 이유가 생긴다 (정산 역류의 표면)
    injected: list[dict[str, Any]] = field(default_factory=list)


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

    injected = [
        {
            "id": n.id,
            "title": n.title,
            "author": n.author,
            "challenged": ontology.is_challenged(n.id),
        }
        for n in chosen
    ]

    session.commit()
    return AskResult(
        turn_id=turn_id,
        question=question,
        answer=answer,
        node_ids=node_ids,
        summary=graph.summary,
        fallback=graph.fallback,
        injected=injected,
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

def stake(
    session: Session,
    node_id: str,
    account: str,
    amount: float,
    *,
    reason: str = "",
) -> dict[str, Any]:
    """확신을 건다. 배당은 검증된 가지에서만 나간다.

    스테이킹은 곧 **링크**다 — 순서가 기록되어 누적 인정 지수(관문 지표)를
    만든다. 무비용 클릭은 절대 링크로 세지 않는다 (대리변수 금지).
    """
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

    session.add(
        db.StakeRow(node_id=node_id, account=account, amount=amount, reason=reason)
    )
    # 스테이킹은 곧 링크다 — 순서가 기록되고 안목이 계산된다.
    # 스테이킹 = 링크. **가중치는 항상 1.0** — 액수가 아니라 순서와 안목이
    # 관문 지표를 만든다 (돈으로 가중치를 사지 못하게).
    session.add(db.LinkRow(node_id=node_id, evaluator=account, weight=1.0))
    # 방금 추가한 링크를 인메모리 스코어러에도 먹여야 응답의 인정 값이 최신이 된다.
    ontology.endorse(account, node_id, weight=1.0)

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
        "recognition": round(recognition_of(ontology, node_id), 3),
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
# 3.5 사람의 반증 / 분기 / 기저 제보
# ---------------------------------------------------------------------------

def refute(
    session: Session,
    node_id: str,
    *,
    author: str,
    reason: str,
    claim: str,
    stake_amount: float,
) -> dict[str, Any]:
    """반증 — **기존 노드를 고치지 않고 형제 노드를 세운다.**

    설계 원칙 3개가 여기 한꺼번에 구현된다:

    * 원본 불변 (reconcile 금지 — 갈릴레오 보존). 반증은 새 노드 + ``refutes``
      엣지이지 편집이 아니다.
    * **입증 책임** — 스테이크 필수(``refute_min_stake`` 이상). 공짜 거부권 금지.
    * "반증했다"가 아니라 **"반증이 검증됐다"가 손상**이다. 반증 노드도 폴립으로
      태어나 스스로 관문을 통과해야 대상을 재심시킨다. 그때까지는 대상에
      ``challenged`` 로 동행할 뿐 승격을 차단하지 않는다 (spec §5).
    """
    target_row = session.get(db.NodeRow, node_id)
    if target_row is None:
        raise ServiceError(f"unknown node {node_id!r}")
    if not claim.strip():
        raise ServiceError("반증에는 다른 답(주장)이 필요하다")
    if stake_amount < settings.refute_min_stake:
        raise ServiceError(
            f"반증에는 최소 {settings.refute_min_stake:.0f}pt 의 확신이 필요하다 "
            "— 입증 책임은 반증하는 쪽에 있다"
        )
    if target_row.author == author:
        raise ServiceError("자기 노드는 반증할 수 없다 (분기를 쓸 것)")

    refuter_id = _add_node(
        session,
        type=NodeType.CLAIM,
        title=claim.strip().split("\n")[0][:200],
        content=claim.strip(),
        author=author,
        turn_id=target_row.turn_id,
    )
    session.add(
        db.EdgeRow(
            id=_uid("e"),
            source_id=refuter_id,
            target_id=node_id,
            type=EdgeType.REFUTES.value,
        )
    )
    session.flush()
    staked = stake(session, refuter_id, author, stake_amount, reason=reason)
    return {
        "refuter_id": refuter_id,
        "target_id": node_id,
        "reason": reason,
        "stake": staked["staked"],
        "note": "기존 답은 그대로 유지된다. 이 반증이 검증을 통과하면 대상이 재심된다.",
    }


def fork(
    session: Session, node_id: str, *, author: str, answer: str, stake_amount: float = 0.0
) -> dict[str, Any]:
    """분기 — 모순 주장이 아니라 **경쟁하는 다른 답**.

    반증(refutes)과 달리 대상을 손상시키지 않는다. 시스템은 답을 하나로
    합치지 않고 형제로 공존시킨다 — 현실이 결판낸다.
    """
    target_row = session.get(db.NodeRow, node_id)
    if target_row is None:
        raise ServiceError(f"unknown node {node_id!r}")
    if not answer.strip():
        raise ServiceError("분기에는 다른 답이 필요하다")

    fork_id = _add_node(
        session,
        type=NodeType.CLAIM,
        title=answer.strip().split("\n")[0][:200],
        content=answer.strip(),
        author=author,
        turn_id=target_row.turn_id,
    )
    session.add(
        db.EdgeRow(
            id=_uid("e"),
            source_id=fork_id,
            target_id=node_id,
            type=EdgeType.RELATES_TO.value,
        )
    )
    session.flush()
    if stake_amount > 0:
        stake(session, fork_id, author, stake_amount)
    else:
        session.commit()
    return {"fork_id": fork_id, "target_id": node_id}


def advise(session: Session, node_id: str, *, requested_by: str = "") -> dict[str, Any]:
    """기저 LLM 제보 요청 — **판정권 없음. 사람 눈앞에 올리기만 한다.**

    결과는 :class:`AdvisoryRow` 로 전량 보존·공개되며, 관문 판정에는 어떤
    경로로도 입력되지 않는다 (:mod:`app.core.promotion` 참조).
    """
    if session.get(db.NodeRow, node_id) is None:
        raise ServiceError(f"unknown node {node_id!r}")

    llm = get_llm()
    if llm.name == "stub":
        return {"advisories": [], "note": "기저 LLM 미연결 — 제보 없음"}

    ontology = build_ontology(session)
    candidate = ontology.require(node_id)
    canonical = [n for n in ontology.canonical() if n.id != node_id]
    if not canonical:
        return {"advisories": [], "note": "검증된 정본이 아직 없다"}

    try:
        advisories: list[Advisory] = damage_mod.make_advisor(llm)(candidate, canonical)
    except Exception as exc:
        log.warning("제보자 호출 실패: %s", exc)
        return {"advisories": [], "note": f"제보자 호출 실패: {exc}"}

    for a in advisories:
        session.add(
            db.AdvisoryRow(
                candidate_id=a.candidate_id,
                canonical_id=a.canonical_id,
                detail=a.detail,
                confidence=a.confidence,
                model=a.model,
                requested_by=requested_by,
            )
        )
    session.commit()
    return {
        "advisories": [
            {
                "canonical_id": a.canonical_id,
                "detail": a.detail,
                "confidence": a.confidence,
                "model": a.model,
            }
            for a in advisories
        ],
        "note": "AI 가 찾은 충돌 후보입니다. 판단은 사람이 합니다 — 동의하면 반증하세요.",
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


def promote(session: Session, node_id: str) -> dict[str, Any]:
    """한 후보에 대한 석회화 판정 → 반영 → 기록 → **재심 캐스케이드**.

    기저 LLM 을 호출하지 않는다. 판정 입력은 사람이 만든 증거(검증된 반증·
    스테이크로 쌓인 인정)와 현실(실측)뿐이다.
    """
    if session.get(db.NodeRow, node_id) is None:
        raise ServiceError(f"unknown node {node_id!r}")

    ontology = build_ontology(session)
    ledger = build_ledger(session)
    registry = build_registry(session)
    node = ontology.require(node_id)

    decision = judge(
        node,
        ontology,
        recognition=recognition_of(ontology, node_id),
        stake=ledger.total_staked(node_id),
        registry=registry,
        policy=_policy(),
        challenged=ontology.is_challenged(node_id),
    )

    economy = Economy(ledger=ledger)
    reclaimed: list[str] = []

    def on_dormant(nid: str) -> None:
        # 잠긴 포인트는 소각하지 않고 유동성 풀로 환원한다 — 가지는 죽지 않는다.
        economy.reclaim_dormant(nid)
        reclaimed.append(nid)

    was_polyp = node.status is NodeStatus.POLYP
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
            reason_code=decision.reason_code.value,
            reason=decision.reason,
            damage_summary=decision.damage.summary(),
            recognition=decision.recognition,
            stake=decision.stake,
            anchored=decision.anchored,
            challenged=decision.challenged,
        )
    )

    # 재심 캐스케이드: 반증이 방금 정본이 되었다면 그것이 겨눈 대상을 즉시 재심한다.
    # 정본도 예외가 아니다 — "느리게 사는 층" (spec §4).
    cascaded: list[str] = []
    if was_polyp and decision.promoted:
        cascaded = ontology.refutation_targets(node_id)

    _flush_balances(session, ledger)
    session.commit()

    result = _decision_view(node_id, decision, node)
    if cascaded:
        result["cascade"] = [promote(session, target) for target in cascaded]
    return result


def _decision_view(
    node_id: str, decision, node: Node
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "verdict": decision.verdict.value,
        "reason_code": decision.reason_code.value,
        "reason": decision.reason,
        "challenged": decision.challenged,
        "damage": {
            "damaged": decision.damage.damaged,
            "summary": decision.damage.summary(),
            "notes": decision.damage.notes,
            "conflicts": [
                {"canonical_id": c.canonical_id, "kind": c.kind, "detail": c.detail}
                for c in decision.damage.conflicts
            ],
        },
        "recognition": round(decision.recognition, 3),
        "stake": decision.stake,
        "anchored": decision.anchored,
        "status": node.status.value,
    }


def promote_all(session: Session) -> list[dict[str, Any]]:
    """폴립층 전체를 한 번 심사한다 (관문 틱)."""
    polyp_ids = [
        row.id
        for row in session.scalars(
            sa_select(db.NodeRow)
            .where(db.NodeRow.status == NodeStatus.POLYP.value)
            .order_by(db.NodeRow.seq)
        ).all()
    ]
    results = []
    for node_id in polyp_ids:
        row = session.get(db.NodeRow, node_id)
        if row is None or row.status != NodeStatus.POLYP.value:
            continue   # 앞선 캐스케이드가 이미 처리했을 수 있다
        results.append(promote(session, node_id))
    return results


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


def my_activity(session: Session, account: str) -> dict[str, Any]:
    """S6 내 활동 — **H3(환류)의 측정면.**

    ``reused`` 가 이 화면의 존재 이유다: 내 기여가 남의 답에 쓰인 횟수가 보이지
    않으면 기여는 반복되지 않는다 (정산 역류의 사용자 표면).
    """
    ensure_account(session, account)
    ontology = build_ontology(session)
    ledger = build_ledger(session)
    registry = build_registry(session)

    # 내 노드가 다른 문답에 주입된 횟수 — Turn.injected_ids 를 역인덱싱한다.
    mine = [n for n in ontology.nodes.values() if n.author == account]
    mine_ids = {n.id for n in mine}
    reuse_count: dict[str, int] = {nid: 0 for nid in mine_ids}
    for turn in session.scalars(sa_select(db.Turn)).all():
        for nid in (turn.injected_ids or "").split(","):
            if nid in reuse_count:
                reuse_count[nid] += 1

    contributions = [
        {
            "id": n.id,
            "title": n.title,
            "status": n.status.value,
            "verified": registry.is_verified(n.id),
            "challenged": ontology.is_challenged(n.id),
            "recognition": round(ontology.authority_of(n.id), 2),
            "reused": reuse_count.get(n.id, 0),
        }
        for n in sorted(mine, key=lambda n: -n.created_at)
    ][:40]

    return {
        "account": account,
        "balance": round(ledger.balance(account), 2),
        "staked": round(ledger.staked_by(account), 2),
        "insight": round(ontology.hub_of(account), 2),
        "reused": sum(reuse_count.values()),
        "contributions": contributions,
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
