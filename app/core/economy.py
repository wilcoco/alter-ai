# 이식 출처: github.com/wilcoco/CAMS-KnowledgeNet
#            (branch claude/ontology-knowledge-evaluation-uzuDx)
#            src/nightwish/economy.py — 의존성 0 으로 설계되어 그대로 옮겼다.
# 수정 시 상류와의 차이를 여기에 적을 것.
"""The closed point economy.

All value comes from *consent* (동의): the point is not injected from outside —
it is minted to participants and made real when it can buy intellectual labour
inside the system. This module models the mechanics the design relies on:

* **UBI issuance** — points are minted to every participant over time, lowering
  the capital barrier and easing the "poor genius" problem.
* **Staking** — receiving points is not the same as betting them; staking locks
  points onto an ontology node as "conviction bound to a contribution".
* **Value-add-gated dividends** — when fresh stake lands downstream, a slice
  flows *up* the chain to earlier contributors (the 256 time-weight: reward for
  early discovery). But the flow has an *eligibility gate*: nodes with no added
  value (a bare "agree" node) are **bypassed** — the flow routes around them
  straight to the nearest value-adding ancestor. This is the structural line
  between a legitimate rent and a Ponzi pass-through.
* **Escrow bounties** — a "현상금": points held in escrow for a targeted call,
  released on adoption or *returned* if not adopted.
* **Burn sink** — a deflationary counterweight to UBI minting.

The dividend distribution is intentionally simple and auditable; the goal is to
make the *invariants* testable, not to ship a production tokenomics.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional


class InsufficientPoints(Exception):
    """Raised when an account cannot cover a debit / stake / escrow."""


@dataclass
class Ledger:
    """Tracks available balances, locked stakes, escrow, and burned supply."""

    available: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    #: node_id -> {contributor -> staked points} (locked on the node)
    staked: dict[str, dict[str, float]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(float))
    )
    #: escrow_id -> (sponsor, amount)
    escrow: dict[str, tuple[str, float]] = field(default_factory=dict)
    burned: float = 0.0
    #: points reclaimed from dormant nodes, awaiting revival restore (not burned)
    liquidity_pool: float = 0.0

    # -- supply ----------------------------------------------------------------
    def mint(self, account: str, amount: float) -> None:
        if amount < 0:
            raise ValueError("cannot mint a negative amount")
        self.available[account] += amount

    def burn(self, account: str, amount: float) -> None:
        self._debit(account, amount)
        self.burned += amount

    def _debit(self, account: str, amount: float) -> None:
        if amount < 0:
            raise ValueError("cannot debit a negative amount")
        if self.available[account] + 1e-9 < amount:
            raise InsufficientPoints(
                f"{account} has {self.available[account]:.2f}, needs {amount:.2f}"
            )
        self.available[account] -= amount

    # -- staking ---------------------------------------------------------------
    def stake(self, account: str, node_id: str, amount: float) -> None:
        if amount <= 0:
            raise ValueError("stake amount must be positive")
        self._debit(account, amount)
        self.staked[node_id][account] += amount

    def stake_on(self, node_id: str) -> dict[str, float]:
        return dict(self.staked[node_id])

    def total_staked(self, node_id: str) -> float:
        return sum(self.staked[node_id].values())

    # -- escrow ----------------------------------------------------------------
    def open_escrow(self, escrow_id: str, sponsor: str, amount: float) -> None:
        if escrow_id in self.escrow:
            raise ValueError(f"escrow {escrow_id} already open")
        self._debit(sponsor, amount)
        self.escrow[escrow_id] = (sponsor, amount)

    def release_escrow(self, escrow_id: str, beneficiary: str) -> float:
        """Pay an open escrow out to ``beneficiary`` (adoption)."""
        sponsor, amount = self.escrow.pop(escrow_id)
        self.available[beneficiary] += amount
        return amount

    def return_escrow(self, escrow_id: str) -> float:
        """Return an unspent escrow to its sponsor (no adoption)."""
        sponsor, amount = self.escrow.pop(escrow_id)
        self.available[sponsor] += amount
        return amount

    # -- views -----------------------------------------------------------------
    def balance(self, account: str) -> float:
        return self.available[account]

    def staked_by(self, account: str) -> float:
        return sum(stakes.get(account, 0.0) for stakes in self.staked.values())

    def accounts(self) -> set[str]:
        seen: set[str] = set(self.available)
        for stakes in self.staked.values():
            seen.update(stakes)
        return seen


@dataclass
class Economy:
    """Policy layer on top of a :class:`Ledger`.

    Holds the tunable monetary parameters and the dividend-routing logic that
    needs to know, per node, whether the node *added value* (the eligibility
    gate) and who its chain ancestors are.
    """

    ledger: Ledger = field(default_factory=Ledger)
    #: fraction of fresh downstream stake that flows up the chain as dividend
    dividend_rate: float = 0.20
    #: fraction of fresh stake burned on each contribution (anti-inflation sink)
    burn_rate: float = 0.02
    #: node_id -> reclaimed stakes, preserved for restore on revival
    _reclaimed: dict[str, dict[str, float]] = field(default_factory=dict)

    def issue_ubi(self, accounts: list[str], amount: float) -> None:
        """Mint a flat UBI grant to every participant (one tick)."""
        for account in accounts:
            self.ledger.mint(account, amount)

    def distribute_dividend(
        self,
        fresh_stake: float,
        staker: str,
        ancestors: list[tuple[str, str, bool]],
        hub_of=lambda contributor: 0.0,
        *,
        ages: Optional[dict[str, int]] = None,
        half_life: Optional[float] = None,
        is_verified: Optional[Callable[[str], bool]] = None,
    ) -> dict[str, float]:
        """Route a dividend from ``fresh_stake`` up an ancestor chain.

        ``ancestors`` is ordered nearest-first as ``(node_id, contributor,
        value_add)`` triples. Nodes whose ``value_add`` is ``False`` are
        **bypassed** — the flow routes around them. The eligible ancestors share
        the dividend pool weighted by *earliness* and *hub* (256 time-weight:
        the earliest, highest-foresight contributor gets the largest slice).

        Optional, backward-compatible policy hardening (see ``docs/critique.md``):

        * ``is_verified`` — the **ground-truth gate** (P0 / critique §1.1, §2),
          applied at *branch* granularity. If given, the whole ancestor chain
          earns only when **at least one** node on it is externally verified
          (e.g. a fix whose yield gain was measured); the earlier problem-
          statement node up the same chain rides on that verification. A branch
          with no verification anywhere pays *nothing* — the structural line that
          keeps the system from being an indistinguishable Ponzi. Pass
          :meth:`VerificationRegistry.is_verified`.
        * ``ages`` + ``half_life`` — **dividend time-decay** (P1 / critique
          §1.3). A node's weight is multiplied by ``0.5 ** (age / half_life)``,
          so a stake that is *not* refreshed by new contribution decays toward
          zero yield. This denies the "lock once, compound forever" capital
          accumulation that pure holding would otherwise enjoy.

        Returns the per-contributor payout actually credited.
        """
        pool = fresh_stake * self.dividend_rate
        if pool <= 0:
            return {}

        # Branch-level ground-truth gate: the chain earns only if *some* node on
        # it is externally verified.
        if is_verified is not None and not any(
            is_verified(node_id) for node_id, _c, value_add in ancestors if value_add
        ):
            return {}

        eligible = [
            (node_id, contributor)
            for node_id, contributor, value_add in ancestors
            if value_add and contributor != staker
        ]
        if not eligible:
            return {}

        # Earliest ancestor is *last* in a nearest-first list, so a deeper
        # (older) ancestor gets a larger time-weight (256 early-discovery reward).
        weights: dict[str, float] = defaultdict(float)
        for depth, (node_id, contributor) in enumerate(eligible):
            earliness = depth + 1  # deeper (older) ancestor -> larger
            decay = 1.0
            if ages is not None and half_life:
                decay = 0.5 ** (ages.get(node_id, 0) / half_life)
            weights[contributor] += earliness * (1.0 + hub_of(contributor)) * decay

        total = sum(weights.values())
        payouts: dict[str, float] = {}
        for contributor, weight in weights.items():
            share = pool * weight / total
            self.ledger.available[contributor] += share
            payouts[contributor] = share
        return payouts

    # -- dormant point recovery (critique §1.2) --------------------------------
    # The inflation-vs-locked-liquidity dilemma: "죽지 않는 가지"는 살리되, 거기
    # 영구히 잠긴 포인트는 *소각하지 않고* 유동성 풀로 환원한다. 부활 시 원
    # 스테이커에게 복원하고 발견자에게 발견 보너스를 지급한다 — 가지는 죽지
    # 않으면서 유동성은 마르지 않게.
    def reclaim_dormant(self, node_id: str) -> float:
        """Move a dormant node's staked points into the liquidity pool.

        The stake record is preserved so a later revival can restore the
        original stakers. Returns the amount reclaimed.
        """
        stakes = self.ledger.staked.get(node_id, {})
        reclaimed = sum(stakes.values())
        self.ledger.liquidity_pool += reclaimed
        # keep the record (for restore) but zero the live stake on the node
        self._reclaimed[node_id] = dict(stakes)
        self.ledger.staked[node_id] = defaultdict(float)
        return reclaimed

    def restore_on_revival(
        self, node_id: str, finder: str, finder_bonus_rate: float = 0.10
    ) -> dict[str, float]:
        """Revive a reclaimed node: restore stakers + pay the finder a bonus.

        Original stakes are re-locked on the node from the liquidity pool, and
        the finder (who recognised a dormant insight) earns a discovery bonus —
        the incentive that keeps the Galileo branch worth reviving.
        """
        reclaimed = self._reclaimed.pop(node_id, None)
        if reclaimed is None:
            raise ValueError(f"{node_id} was not reclaimed; nothing to restore")
        restored = sum(reclaimed.values())
        if self.ledger.liquidity_pool + 1e-9 < restored:
            raise InsufficientPoints("liquidity pool cannot cover restore")
        self.ledger.liquidity_pool -= restored
        for staker, amount in reclaimed.items():
            self.ledger.staked[node_id][staker] += amount
        bonus = restored * finder_bonus_rate
        if self.ledger.liquidity_pool >= bonus:
            self.ledger.liquidity_pool -= bonus
            self.ledger.available[finder] += bonus
        else:  # pool short: mint the discovery bonus
            self.ledger.mint(finder, bonus)
        return {"restored": restored, "finder_bonus": bonus}
