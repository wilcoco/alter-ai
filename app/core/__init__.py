"""도메인 코어 — 의존성 0.

``scoring`` · ``economy`` · ``verification`` 은 CAMS-KnowledgeNet 에서 그대로
이식했고(각 파일 상단 출처 표기), ``ontology`` · ``promotion`` 은 coral 이 더한
산호초 층위(폴립/정본/잠복)와 석회화 판정 기관이다.

이 패키지는 FastAPI·SQLAlchemy·anthropic 를 import 하지 않는다. 메커니즘을
읽고 테스트할 수 있게 유지하기 위한 규약이며 tests/test_isolation.py 가 강제한다.
"""

from app.core.economy import Economy, InsufficientPoints, Ledger
from app.core.ontology import (
    Edge,
    EdgeType,
    Node,
    NodeStatus,
    NodeType,
    Ontology,
    OntologyError,
    PIC_TYPES,
)
from app.core.promotion import (
    Conflict,
    DamageProbe,
    DamageReport,
    PromotionDecision,
    PromotionPolicy,
    Verdict,
    apply,
    damage_test,
    judge,
    strict_gate,
)
from app.core.scoring import ScoreEngine
from app.core.verification import Direction, Measurement, VerificationRegistry

__all__ = [
    "Conflict",
    "DamageProbe",
    "DamageReport",
    "Direction",
    "Economy",
    "Edge",
    "EdgeType",
    "InsufficientPoints",
    "Ledger",
    "Measurement",
    "Node",
    "NodeStatus",
    "NodeType",
    "Ontology",
    "OntologyError",
    "PIC_TYPES",
    "PromotionDecision",
    "PromotionPolicy",
    "ScoreEngine",
    "Verdict",
    "VerificationRegistry",
    "apply",
    "damage_test",
    "judge",
    "strict_gate",
]
