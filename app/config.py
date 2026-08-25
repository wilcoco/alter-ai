"""환경 설정 — 전부 환경변수, 전부 기본값 있음.

원칙: **키 없이도 뜬다.** ANTHROPIC_API_KEY 가 없으면 기저 LLM 은 stub 로
떨어지고, DATABASE_URL 이 없으면 SQLite 파일로 떨어진다. 배포가 설정에
인질로 잡히지 않게 하려는 것 — 한 바퀴의 구조는 기저가 stub 여도 그대로 돈다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _normalize_db_url(url: str) -> str:
    """Railway/Heroku 의 ``postgres://`` 를 SQLAlchemy 2.x 드라이버 URL 로."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


@dataclass(frozen=True)
class Settings:
    # -- 기저 LLM (교체 가능 부품: spec §0) --------------------------------
    anthropic_api_key: str | None
    base_model: str
    max_tokens: int

    # -- 저장소 -----------------------------------------------------------
    database_url: str

    # -- 석회화(승격) 정책 -------------------------------------------------
    #: 승격에 필요한 최소 **누적 인정 지수** (순서·허브 가중 권위).
    #:
    #: 단위 주의: 이것은 포인트가 아니다. 권위는 인정 1건당 약 1.0 씩 쌓이고
    #: (인정자의 안목만큼 가산), 스테이크 액수는 여기 곱해지지 **않는다** —
    #: 돈으로 관문 가중치를 살 수 없다는 tree.py 의 규칙("포인트 크기 = 기여
    #: 크기")을 관문 지표에서도 지키기 위해서다. 스테이크는 진입 비용이자
    #: 확신의 증거로 병존하되, 관문이 세는 것은 **누가 얼마나 일찍 알아봤는가**다.
    recognition_threshold: float
    #: 큰 스테이크는 기여를 동반해야 한다 (tree.py 의 "포인트 크기 = 기여 크기")
    large_stake_threshold: float
    #: 잠복기 — 후보가 폴립층에서 관찰당해야 하는 최소 틱 수 (시간이 검증자)
    quarantine_ticks: int
    #: 신규 계정 UBI
    ubi_grant: float
    #: 컨텍스트 주입 시 끌어올 정본 노드 최대 개수
    injection_top_k: int
    #: 검색 결과 개수
    search_top_k: int
    #: 검색 결과 중 신규·잠복·저인정 노드에 강제 배정할 비율 (마태 효과 보정)
    explore_quota: float
    #: 반증에 요구하는 최소 스테이크 — 입증 책임의 경제적 구현
    refute_min_stake: float

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


def load_settings() -> Settings:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url:
        db_url = _normalize_db_url(db_url)
    else:
        data_dir = Path(os.environ.get("CORAL_DATA_DIR", "./data")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{data_dir / 'coral.db'}"

    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        base_model=os.environ.get("CORAL_BASE_MODEL", "claude-opus-5"),
        max_tokens=_i("CORAL_MAX_TOKENS", 8000),
        database_url=db_url,
        recognition_threshold=_f("CORAL_RECOGNITION_THRESHOLD", 3.0),
        large_stake_threshold=_f("CORAL_LARGE_STAKE_THRESHOLD", 25.0),
        quarantine_ticks=_i("CORAL_QUARANTINE_TICKS", 1),
        ubi_grant=_f("CORAL_UBI_GRANT", 100.0),
        injection_top_k=_i("CORAL_INJECTION_TOP_K", 8),
        search_top_k=_i("CORAL_SEARCH_TOP_K", 12),
        explore_quota=_f("CORAL_EXPLORE_QUOTA", 0.30),
        refute_min_stake=_f("CORAL_REFUTE_MIN_STAKE", 10.0),
    )


settings = load_settings()
