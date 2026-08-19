"""영속 스키마 — SQLite(기본) / Postgres(DATABASE_URL 주입 시).

도메인 코어는 저장소를 모른다. 이 모듈이 코어 객체(Ontology·Ledger·
VerificationRegistry)를 **매 요청마다 DB 에서 조립하고 다시 해체해** 넣는다.
느리지만 MVP 규모에서 정확하고, 코어의 의존성 0 규약을 지킨다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Account(Base):
    """참여자. 포인트는 닫힌 경제 안에서만 돈다."""

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Turn(Base):
    """문답 한 번 — 한 바퀴의 입구."""

    __tablename__ = "turns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    author: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    base_answer: Mapped[str] = mapped_column(Text)
    #: 이 답을 만들 때 주입된 정본 노드 id 들 (쉼표 구분) — 승격 (a)경로의 실측
    injected_ids: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    stubbed: Mapped[bool] = mapped_column(Boolean, default=False)
    #: 사용자가 답을 그대로 채택했는가, 증분했는가 (spec §3 라우팅의 원시 신호)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    increment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NodeRow(Base):
    """온톨로지 노드. H2A2H2 스키마와 포맷 호환."""

    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("turns.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), index=True, default="polyp")
    value_add: Mapped[bool] = mapped_column(Boolean, default=True)
    observed_ticks: Mapped[int] = mapped_column(Integer, default=0)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    verdicts: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EdgeRow(Base):
    __tablename__ = "edges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), ForeignKey("nodes.id"))
    target_id: Mapped[str] = mapped_column(String(64), ForeignKey("nodes.id"))
    type: Mapped[str] = mapped_column(String(32))


class StakeRow(Base):
    """노드에 잠긴 확신. 받는 것과 거는 것은 다르다."""

    __tablename__ = "stakes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(64), ForeignKey("nodes.id"), index=True)
    account: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[float] = mapped_column(Float)
    #: 잠복 회수로 유동성 풀에 환원됐는가 (기록은 부활 복원을 위해 남긴다)
    reclaimed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class LinkRow(Base):
    """링크 순서 기록 — 허브/권위 증분 스코어러의 입력. **순서가 곧 안목이다.**"""

    __tablename__ = "links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    evaluator: Mapped[str] = mapped_column(String(64), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MeasurementRow(Base):
    """외부 현실 닻 — 동의의 순환을 닫는 유일한 신호."""

    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(64), ForeignKey("nodes.id"), index=True)
    metric: Mapped[str] = mapped_column(String(128))
    baseline: Mapped[float] = mapped_column(Float)
    observed: Mapped[float] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(16), default="higher_better")
    unit: Mapped[str] = mapped_column(String(32), default="")
    min_rel_improvement: Mapped[float] = mapped_column(Float, default=0.0)
    reporter: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PromotionRow(Base):
    """석회화 판정 이력. 판정은 지워지지 않는다 — 감사 가능해야 한다."""

    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(64), ForeignKey("nodes.id"), index=True)
    verdict: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    damage_summary: Mapped[str] = mapped_column(Text, default="")
    probed: Mapped[bool] = mapped_column(Boolean, default=True)
    stake: Mapped[float] = mapped_column(Float, default=0.0)
    anchored: Mapped[bool] = mapped_column(Boolean, default=False)
    payouts: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Meta(Base):
    """전역 카운터 (노드 시퀀스, 논리 시계)."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float] = mapped_column(Float, default=0.0)


_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)
engine = create_engine(
    settings.database_url, connect_args=_connect_args, pool_pre_ping=True, future=True
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)
