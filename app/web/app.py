"""FastAPI 앱 — coral MVP 서비스 트랙.

화면은 H2A2H2 의 3-패널을 산호초 층위로 다시 배치한 것이다:

    좌: 신규 안정층 (정본)     — 승격을 통과해 굳은 지식. 다음 질문에 주입되는 것.
    중: 폴립층 (활성면)        — 후보 노드. 스테이킹·실측·승격 심사가 여기서 일어난다.
    우: 문답                   — 한 바퀴의 입구. 기저 답 + 사용자 증분.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.capture.llm import get_llm
from app.config import settings
from app.store import db
from app.store.service import ServiceError
from app.web.api import router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coral")

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))



@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    log.info(
        "coral %s 기동 — 기저 LLM: %s · 저장소: %s",
        __version__,
        get_llm().name,
        settings.database_url.split("://")[0],
    )
    yield


app = FastAPI(
    title="coral",
    version=__version__,
    lifespan=lifespan,
    description=(
        "인간-AI 대화의 지식을 구조로 포획(P→I→C)하고, 시장으로 검증하고, "
        "승격시키는 살아있는 집단 학습 시스템 — MVP '최소 한 바퀴'."
    ),
)


@app.exception_handler(ServiceError)
async def _service_error(_request: Request, exc: ServiceError) -> JSONResponse:
    """규칙 위반은 500 이 아니라 400 — 사용자에게 그대로 보여준다."""
    return JSONResponse(status_code=400, content={"error": str(exc)})


app.include_router(router)


def _ctx() -> dict:
    return {
        "version": __version__,
        "llm": get_llm().name,
        "llm_enabled": settings.llm_enabled,
        "recognition_threshold": settings.recognition_threshold,
        "quarantine_ticks": settings.quarantine_ticks,
        "refute_min_stake": settings.refute_min_stake,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """사용자 화면 (S1~S6). 기계 어휘는 여기 나오지 않는다."""
    return TEMPLATES.TemplateResponse(request, "app.html", _ctx())


@app.get("/console", response_class=HTMLResponse)
def console(request: Request) -> HTMLResponse:
    """운영자 화면 (S7) — 기존 3-패널. 테스트 중 기계가 도는지 우리가 본다."""
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        _ctx()
        | {
            "stake_threshold": settings.recognition_threshold,
        },
    )
