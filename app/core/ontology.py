"""살아있는 온톨로지 — P→I→C 지식 그래프.

**스키마는 H2A2H2 와 포맷 호환을 유지한다** (CLAUDE.md "기존 자산 재사용"):

* node type: ``concept · claim · evidence · source · qa · premise · inference ·
  conclusion``
* edge type: ``supports · refutes · relates_to · cites · infers``

KnowledgeNet 의 ``tree.py`` 에서 가져온 두 규칙:

* **포인트 크기 = 기여 크기** — :data:`Ontology.large_stake_threshold` 이상의
  스테이크는 반드시 기여(``value_add``)를 동반해야 한다. 돈만으로 큰 가중치를
  살 수 없다는 이 한 줄이 금권·인기투표·노이즈를 동시에 막는다.
* **삭제 없음, 잠복만** — 노드는 지워지거나 덮어써지지 않는다. 기각되면
  :attr:`NodeStatus.DORMANT` 로 잠들고, 언제든 부활할 수 있다 (갈릴레오 문제:
  진리의 시간 비대칭성 보존).

여기에 coral 이 더하는 것이 **산호초 층위**다. 노드는 세 상태를 오간다:

    POLYP  ──승격(석회화 판정 통과)──▶  CANONICAL   (= 신규 안정층, spec §1)
      │                                     │
      └──기각(손상)──▶ DORMANT ◀──재기각─────┘
                         │
                         └──부활──▶ POLYP

``CANONICAL`` 만이 다음 질문의 컨텍스트로 주입된다 — 승격 (a)경로.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.scoring import ScoreEngine


class OntologyError(Exception):
    """규칙을 위반한 조작."""


class NodeType(str, Enum):
    """H2A2H2 ``NodeType`` 과 1:1."""

    CONCEPT = "concept"
    CLAIM = "claim"
    EVIDENCE = "evidence"
    SOURCE = "source"
    QA = "qa"
    PREMISE = "premise"
    INFERENCE = "inference"
    CONCLUSION = "conclusion"


class EdgeType(str, Enum):
    """H2A2H2 ``EdgeType`` 과 1:1."""

    SUPPORTS = "supports"
    REFUTES = "refutes"
    RELATES_TO = "relates_to"
    CITES = "cites"
    INFERS = "infers"


class NodeStatus(str, Enum):
    """산호초 층위."""

    #: 폴립층 — 살아있는 활성면. 아직 검증되지 않은 후보.
    POLYP = "polyp"
    #: 신규 안정층 — 석회화 판정을 통과해 정본에 굳은 지식.
    CANONICAL = "canonical"
    #: 잠복 — 기각되었으나 죽지 않은 가지. 부활 가능.
    DORMANT = "dormant"


#: P→I→C 삼단의 노드 타입 (추론 구조 자체의 데이터 — spec §4)
PIC_TYPES = (NodeType.PREMISE, NodeType.INFERENCE, NodeType.CONCLUSION)


@dataclass
class Node:
    """온톨로지 노드 하나."""

    id: str
    type: NodeType
    title: str
    content: str = ""
    author: str = "anon"
    #: 이 노드를 낳은 문답 (qa 노드 id). 귀속의 뿌리.
    turn_id: str | None = None
    status: NodeStatus = NodeStatus.POLYP
    #: 기여인가 단순 동의인가 — 배당 우회 게이트(economy)의 입력
    value_add: bool = True
    created_at: int = 0
    #: 폴립층에 머문 틱 수 (잠복기 관찰 — 시간이 검증자)
    observed_ticks: int = 0
    #: 승격/기각 판정 이력 (사람이 읽는 감사 로그)
    verdicts: list[str] = field(default_factory=list)

    @property
    def is_canonical(self) -> bool:
        return self.status is NodeStatus.CANONICAL


@dataclass(frozen=True)
class Edge:
    """방향 있는 관계. ``refutes`` 는 손상 판정의 1차 입력이다."""

    id: str
    source_id: str
    target_id: str
    type: EdgeType


@dataclass
class Ontology:
    """노드·엣지 그래프 + 허브/권위 스코어러.

    저장소를 모른다 (KnowledgeNet 코어의 설계 원칙 유지) — 순수 인메모리
    도메인 객체이고, 영속화는 :mod:`app.store` 가 이 객체를 조립·해체한다.
    """

    scoring: ScoreEngine = field(default_factory=ScoreEngine)
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[str, Edge] = field(default_factory=dict)
    #: 이 이상 스테이크는 기여 동반 필수
    large_stake_threshold: float = 25.0
    _clock: int = 0

    # -- 내부 -----------------------------------------------------------------
    def tick(self) -> int:
        self._clock += 1
        return self._clock

    @property
    def clock(self) -> int:
        return self._clock

    def require(self, node_id: str) -> Node:
        if node_id not in self.nodes:
            raise OntologyError(f"unknown node {node_id!r}")
        return self.nodes[node_id]

    def check_stake_rule(self, stake: float, value_add: bool) -> None:
        """포인트 크기 = 기여 크기 (tree.py 이식 규칙)."""
        if stake >= self.large_stake_threshold and not value_add:
            raise OntologyError(
                f"{stake:.0f} 이상({self.large_stake_threshold:.0f})의 스테이크는 "
                "기여(value_add)를 동반해야 한다 — 돈만으로 가중치를 살 수 없다"
            )

    # -- 생성 -----------------------------------------------------------------
    def add_node(
        self,
        node_id: str,
        type: NodeType,
        title: str,
        *,
        content: str = "",
        author: str = "anon",
        turn_id: str | None = None,
        value_add: bool = True,
        status: NodeStatus = NodeStatus.POLYP,
    ) -> Node:
        if node_id in self.nodes:
            raise OntologyError(f"node {node_id!r} already exists")
        node = Node(
            id=node_id,
            type=type,
            title=title,
            content=content,
            author=author,
            turn_id=turn_id,
            status=status,
            value_add=value_add,
            created_at=self.tick(),
        )
        self.nodes[node_id] = node
        return node

    def add_edge(
        self, edge_id: str, source_id: str, target_id: str, type: EdgeType
    ) -> Edge:
        self.require(source_id)
        self.require(target_id)
        if edge_id in self.edges:
            raise OntologyError(f"edge {edge_id!r} already exists")
        edge = Edge(id=edge_id, source_id=source_id, target_id=target_id, type=type)
        self.edges[edge_id] = edge
        return edge

    # -- 평가 -----------------------------------------------------------------
    def endorse(self, evaluator: str, node_id: str, *, weight: float = 1.0) -> None:
        """평가자가 노드를 인정했다 — 링크 순서 가중 스코어러에 먹인다.

        허브(안목)와 권위(내용 가치)가 증분 갱신된다. 늦게 올라타면 거의 못 번다.
        """
        self.require(node_id)
        self.scoring.link(evaluator, node_id, weight=weight)

    def authority_of(self, node_id: str) -> float:
        return self.scoring.authority_of(node_id)

    def hub_of(self, account: str) -> float:
        return self.scoring.hub_of(account)

    # -- 산호초 층위 전이 ------------------------------------------------------
    def promote(self, node_id: str, reason: str) -> Node:
        """폴립 → 신규 안정층. 승격 (a)경로: 온톨로지 정본에 굳음."""
        node = self.require(node_id)
        node.status = NodeStatus.CANONICAL
        node.verdicts.append(f"[t{self._clock}] PROMOTED — {reason}")
        return node

    def make_dormant(self, node_id: str, reason: str) -> Node:
        """기각 = 잠복. **삭제도 덮어쓰기도 아니다.**"""
        node = self.require(node_id)
        node.status = NodeStatus.DORMANT
        node.verdicts.append(f"[t{self._clock}] DORMANT — {reason}")
        return node

    def revive(self, node_id: str, finder: str) -> Node:
        """잠복 가지를 다시 활성면으로 (갈릴레오 가지의 부활)."""
        node = self.require(node_id)
        if node.status is not NodeStatus.DORMANT:
            raise OntologyError(f"{node_id!r} is not dormant")
        node.status = NodeStatus.POLYP
        node.observed_ticks = 0
        node.verdicts.append(f"[t{self._clock}] REVIVED by {finder}")
        return node

    # -- 조회 -----------------------------------------------------------------
    def canonical(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.status is NodeStatus.CANONICAL]

    def polyps(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.status is NodeStatus.POLYP]

    def dormant(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.status is NodeStatus.DORMANT]

    def edges_of(self, node_id: str) -> list[Edge]:
        return [
            e
            for e in self.edges.values()
            if e.source_id == node_id or e.target_id == node_id
        ]

    def refuters_of(self, node_id: str) -> list[Edge]:
        """이 노드를 ``refutes`` 로 겨누는 엣지들 — 손상 판정의 1차 입력."""
        return [
            e
            for e in self.edges.values()
            if e.type is EdgeType.REFUTES and e.target_id == node_id
        ]

    def ancestors(self, node_id: str) -> list[tuple[str, str, bool]]:
        """배당 라우팅용 조상 사슬 — ``(node_id, author, value_add)`` 가까운 순.

        ``infers`` 엣지를 거슬러 올라간다 (P→I→C 사슬이 곧 기여 사슬).
        """
        chain: list[tuple[str, str, bool]] = []
        seen = {node_id}
        cursor = node_id
        while True:
            parents = [
                e.source_id
                for e in self.edges.values()
                if e.type is EdgeType.INFERS
                and e.target_id == cursor
                and e.source_id not in seen
            ]
            if not parents:
                break
            cursor = parents[0]
            seen.add(cursor)
            parent = self.nodes.get(cursor)
            if parent is None:
                break
            chain.append((parent.id, parent.author, parent.value_add))
        return chain
