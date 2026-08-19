"""JSON API — 한 바퀴의 각 단계가 엔드포인트 하나씩.

    POST /api/ask                 질문 → (정본 주입) 기저 답 → P→I→C 포획
    POST /api/turns/{id}/respond  채택 / 증분
    POST /api/nodes/{id}/stake    스테이킹
    POST /api/nodes/{id}/measure  외부 현실 닻
    POST /api/nodes/{id}/promote  석회화 판정
    POST /api/promote-all         폴립층 일괄 심사 (관문 틱)
    POST /api/nodes/{id}/revive   잠복 가지 부활
    GET  /api/state               3-패널 스냅샷
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


class MeasureIn(BaseModel):
    metric: str
    baseline: float
    observed: float
    direction: str = "higher_better"
    unit: str = ""
    min_rel_improvement: float = 0.0
    reporter: str = ""


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


@router.get("/state")
def state(session: Session = Depends(get_session)) -> dict:
    return service.snapshot(session)
