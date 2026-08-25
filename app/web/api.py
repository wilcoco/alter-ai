"""JSON API — 한 바퀴의 각 단계가 엔드포인트 하나씩.

    GET  /api/search              검색 선점 — LLM 부르기 전에 기존 문답부터
    POST /api/ask                 질문 → (정본 주입) 기저 답 → P→I→C 포획
    POST /api/turns/{id}/respond  채택 / 증분
    POST /api/nodes/{id}/stake    스테이킹
    POST /api/nodes/{id}/measure  외부 현실 닻
    POST /api/nodes/{id}/refute   반증 — 형제 노드 + 입증 책임 스테이크
    POST /api/nodes/{id}/fork     분기 — 경쟁하는 다른 답
    POST /api/nodes/{id}/advise   기저 제보 요청 (판정권 없음)
    POST /api/nodes/{id}/promote  석회화 판정
    POST /api/promote-all         폴립층 일괄 심사 (관문 틱)
    POST /api/nodes/{id}/revive   잠복 가지 부활
    GET  /api/me                  내 활동 (기여·포인트·재사용 목격)
    GET  /api/state               운영자 스냅샷
    GET  /api/health              헬스체크 (Railway)
"""

from __future__ import annotations

from typing import Generator

from fastapi import APIRouter, Depends
from sqlalchemy import text
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import __version__
from app.capture.llm import get_llm
from app.config import settings
from app.store import db, service

router = APIRouter(prefix="/api")


def get_session() -> Generator[Session, None, None]:
    session = db.SessionLocal()
    try:
        yield session
    finally:
        session.close()


# -- 요청 바디 ---------------------------------------------------------------

class AskIn(BaseModel):
    question: str = Field(min_length=1)
    author: str = Field(default="anon", min_length=1, max_length=64)


class RespondIn(BaseModel):
    author: str = Field(default="anon", max_length=64)
    #: 기저 답을 그대로 채택하는가
    accept: bool = True
    #: 가치 증분 (교정·보강). 비어 있으면 순수 채택.
    increment: str = ""


class StakeIn(BaseModel):
    account: str = Field(default="anon", max_length=64)
    amount: float = Field(gt=0)
    comment: str = Field(default="", max_length=200)


class MeasureIn(BaseModel):
    metric: str
    baseline: float
    observed: float
    direction: str = "higher_better"
    unit: str = ""
    min_rel_improvement: float = 0.0
    reporter: str = ""


class RefuteIn(BaseModel):
    author: str = Field(default="anon", max_length=64)
    #: 사실 오류 / 조건 누락 / 재현 안 됨 (단일 어휘 레지스트리)
    reason: str = Field(default="", max_length=32)
    #: 다른 답 — 반증은 원본을 고치지 않고 형제 노드를 세운다
    claim: str = Field(min_length=1)
    #: 입증 책임. settings.refute_min_stake 이상이어야 한다
    stake: float = Field(gt=0)


class ForkIn(BaseModel):
    author: str = Field(default="anon", max_length=64)
    answer: str = Field(min_length=1)
    stake: float = Field(default=0.0, ge=0)


class AdviseIn(BaseModel):
    requested_by: str = Field(default="anon", max_length=64)


class ReviveIn(BaseModel):
    finder: str = Field(default="anon", max_length=64)


# -- 엔드포인트 --------------------------------------------------------------

@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    """Railway 헬스체크. DB 왕복까지 확인한다 — 앱만 뜨고 DB 가 죽은 상태를 잡는다."""
    session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "version": __version__,
        "llm": get_llm().name,
        "llm_enabled": settings.llm_enabled,
        "store": settings.database_url.split("://")[0],
    }


@router.post("/ask")
def ask(body: AskIn, session: Session = Depends(get_session)) -> dict:
    result = service.ask(session, body.question, body.author)
    return {
        "turn_id": result.turn_id,
        "question": result.question,
        "answer": result.answer.text,
        "model": result.answer.model,
        "stubbed": result.answer.stubbed,
        "injected_ids": result.answer.injected_ids,
        "injected": result.injected,
        "node_ids": result.node_ids,
        "summary": result.summary,
        "structuring_fallback": result.fallback,
    }


@router.post("/turns/{turn_id}/respond")
def respond(
    turn_id: str, body: RespondIn, session: Session = Depends(get_session)
) -> dict:
    node_id = service.respond(
        session,
        turn_id,
        accept=body.accept,
        increment=body.increment,
        author=body.author,
    )
    return {"turn_id": turn_id, "increment_node_id": node_id}


@router.post("/nodes/{node_id}/stake")
def stake(node_id: str, body: StakeIn, session: Session = Depends(get_session)) -> dict:
    return service.stake(session, node_id, body.account, body.amount)


@router.post("/nodes/{node_id}/measure")
def measure(
    node_id: str, body: MeasureIn, session: Session = Depends(get_session)
) -> dict:
    return service.measure(
        session,
        node_id,
        metric=body.metric,
        baseline=body.baseline,
        observed=body.observed,
        direction=body.direction,
        unit=body.unit,
        min_rel_improvement=body.min_rel_improvement,
        reporter=body.reporter,
    )


@router.get("/search")
def search(q: str = "", session: Session = Depends(get_session)) -> dict:
    """검색 선점. **LLM 을 호출하지 않는다** — 히트하면 여기서 끝나는 게 성공이다."""
    return service.search(session, q)


@router.post("/nodes/{node_id}/refute")
def refute(
    node_id: str, body: RefuteIn, session: Session = Depends(get_session)
) -> dict:
    return service.refute(
        session,
        node_id,
        author=body.author,
        reason=body.reason,
        claim=body.claim,
        stake_amount=body.stake,
    )


@router.post("/nodes/{node_id}/fork")
def fork(node_id: str, body: ForkIn, session: Session = Depends(get_session)) -> dict:
    return service.fork(
        session, node_id, author=body.author, answer=body.answer, stake_amount=body.stake
    )


@router.post("/nodes/{node_id}/advise")
def advise(node_id: str, body: AdviseIn, session: Session = Depends(get_session)) -> dict:
    """AI 가 찾은 충돌 후보. 판단은 사람이 한다 — 이 결과는 관문에 입력되지 않는다."""
    return service.advise(session, node_id, requested_by=body.requested_by)


@router.post("/nodes/{node_id}/promote")
def promote(node_id: str, session: Session = Depends(get_session)) -> dict:
    return service.promote(session, node_id)


@router.post("/promote-all")
def promote_all(session: Session = Depends(get_session)) -> dict:
    results = service.promote_all(session)
    return {"judged": len(results), "results": results}


@router.post("/nodes/{node_id}/revive")
def revive(node_id: str, body: ReviveIn, session: Session = Depends(get_session)) -> dict:
    return service.revive(session, node_id, body.finder)


@router.get("/me")
def me(account: str = "anon", session: Session = Depends(get_session)) -> dict:
    """내 활동 — 포인트·안목·**내 지식이 쓰인 횟수**(H3 측정면)."""
    return service.my_activity(session, account)


@router.get("/state")
def state(session: Session = Depends(get_session)) -> dict:
    return service.snapshot(session)
