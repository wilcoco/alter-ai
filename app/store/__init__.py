"""영속 층 — 도메인 코어와 DB 사이의 유일한 다리.

``db``       SQLAlchemy 스키마 + 엔진 (SQLite 기본, DATABASE_URL 이면 Postgres)
``service``  한 바퀴 오케스트레이션 (ask → respond → stake/measure → promote → 주입)
"""

from app.store import db, service

__all__ = ["db", "service"]
