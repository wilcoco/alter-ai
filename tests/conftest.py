"""테스트는 항상 격리된 임시 DB 위에서 돈다."""

from __future__ import annotations

import os
import tempfile

# app.config 가 import 시점에 환경을 읽으므로, 그 전에 세팅해야 한다.
_TMP = tempfile.mkdtemp(prefix="coral-test-")
os.environ["CORAL_DATA_DIR"] = _TMP
os.environ.pop("DATABASE_URL", None)
os.environ.pop("ANTHROPIC_API_KEY", None)   # 테스트는 stub 기저로 돈다

import pytest  # noqa: E402

from app.store import db  # noqa: E402


@pytest.fixture()
def session():
    """매 테스트마다 빈 스키마."""
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)
    s = db.SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.web.app import app

    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)
    with TestClient(app) as c:
        yield c
