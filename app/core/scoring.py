# 이식 출처: github.com/wilcoco/CAMS-KnowledgeNet
#            (branch claude/ontology-knowledge-evaluation-uzuDx)
#            src/nightwish/scoring.py — 의존성 0 으로 설계되어 그대로 옮겼다.
# 수정 시 상류와의 차이를 여기에 적을 것.
"""Incremental hub / authority scoring (patent 10-0913256).

The classic HITS (Kleinberg) and PageRank algorithms need accumulated data and
repeated, converging matrix iteration — so they cannot score brand-new content
in real time. The 2005 patent's contribution is to **replace the converging
iteration with link *order*** ("링크 순서 가중"):

* Whoever links to a sub-node *earlier* — and keeps being validated as later
  links pile onto the same sub-node — earns a rising **hub** index. Hub is the
  evaluator's *foresight* (안목): the ability to recognise something good early.
* A sub-node recommended by good hubs earns a rising **authority** index.
  Authority is the *content's value* (권위).

Hub and authority reinforce each other, and every update is **incremental** —
no global re-computation, so a freshly created node is scored the instant the
first link arrives. Crucially, the currency of evaluation is *foresight*, not
*popularity*, which structurally resists bandwagon ("밴드왜건") abuse: piling on
late earns almost nothing.

This module is deliberately storage-agnostic: it keeps its own small maps keyed
by opaque string ids so it can be unit-tested in isolation from the tree.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class ScoreEngine:
    """Order-sensitive, incremental hub/authority scorer.

    A "link" is the event *evaluator E recognised content node N* (in this
    system: someone follows, forks from, or contributes to N). Links are fed in
    the order they happen; the engine never iterates to convergence.
    """

    #: node_id -> authority (value of the content)
    authority: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    #: contributor_id -> hub (foresight of the evaluator)
    hub: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    #: node_id -> evaluators in the order they linked (the "link order")
    _linkers: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def link(self, evaluator: str, node_id: str, *, weight: float = 1.0) -> None:
        """Record that ``evaluator`` linked to ``node_id`` (the next in order).

        Two incremental effects, both order-sensitive:

        1. **Reward foresight.** Every *earlier* linker is validated by this new
           follower. The j-th earlier linker receives ``weight / j`` of hub —
           so the first discoverer keeps earning on every later follower while
           late joiners earn almost nothing. This is what turns "popularity"
           into "who saw it first".
        2. **Confer authority.** ``node_id`` gains authority equal to a base
           amount plus the *current hub* of the linker — a high-hub evaluator's
           endorsement is worth more than a stranger's.
        """
        if weight <= 0:
            raise ValueError("link weight must be positive")

        for j, earlier in enumerate(self._linkers[node_id], start=1):
            self.hub[earlier] += weight / j

        self._linkers[node_id].append(evaluator)
        self.authority[node_id] += weight * (1.0 + self.hub[evaluator])

    def link_order(self, node_id: str) -> list[str]:
        """Return the evaluators of ``node_id`` in the order they linked."""
        return list(self._linkers[node_id])

    def linker_position(self, evaluator: str, node_id: str) -> int | None:
        """1-based position of ``evaluator`` among ``node_id``'s linkers, or None."""
        order = self._linkers[node_id]
        return order.index(evaluator) + 1 if evaluator in order else None

    def authority_of(self, node_id: str) -> float:
        return self.authority[node_id]

    def hub_of(self, evaluator: str) -> float:
        return self.hub[evaluator]
