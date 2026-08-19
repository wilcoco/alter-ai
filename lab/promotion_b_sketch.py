"""승격 (b)경로 실험 하네스 — **답이 아니라 관찰 도구다.**

가설 (spec §4): **검증 강도 = 인공 신경조절물질.** 스테이킹·실측으로 확립된
검증 강도가 학습률(가소성)을 국소적으로 조절하면, 검증 안 된 지식은 가중치를
거의 안 움직이고 검증된 지식만 굳는다.

여기서 하는 것: 작은 2층 MLP 에 지식 항목을 **순차로** 흘려넣으며
(i.i.d. 셔플 금지 — 그게 현 체제가 딜레마를 회피하는 방법이므로),
게이팅 유무에 따른 보존/흡수 곡선을 눈으로 본다.

⚠ 이것은 세션 3 커리큘럼의 "이해 가속용 소실험" 범주다. 대안 학습 규칙을
제안하는 코드가 아니다. numpy 조차 쓰지 않고 순수 파이썬으로 둔 것은, 이
파일이 *성능*이 아니라 *메커니즘*을 보기 위한 것임을 분명히 하려는 것이다.

실행: ``python lab/promotion_b_sketch.py``
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

SEED = 20260819


@dataclass
class Item:
    """한 지식 항목. ``verification`` 이 인공 신경조절물질의 농도다."""

    name: str
    inputs: list[float]
    target: float
    #: 0.0(미검증) ~ 1.0(실측 닻 통과). 게이팅 모드에서 학습률을 곱한다.
    verification: float = 1.0


@dataclass
class TinyNet:
    """은닉 1층 MLP. 역전파를 쓴다 — 지금은 *비교 기준선*이 필요하기 때문이다.

    대안 학습 규칙을 여기 넣는 것이 다음 단계이고, 그때 이 클래스는 갈린다.
    """

    n_in: int
    n_hidden: int = 6
    lr: float = 0.1
    w1: list[list[float]] = field(default_factory=list)
    w2: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        rng = random.Random(SEED)
        self.w1 = [
            [rng.uniform(-0.5, 0.5) for _ in range(self.n_in)]
            for _ in range(self.n_hidden)
        ]
        self.w2 = [rng.uniform(-0.5, 0.5) for _ in range(self.n_hidden)]

    @staticmethod
    def _act(x: float) -> float:
        return math.tanh(x)

    def forward(self, x: list[float]) -> tuple[float, list[float]]:
        hidden = [
            self._act(sum(w * xi for w, xi in zip(row, x))) for row in self.w1
        ]
        out = sum(w * h for w, h in zip(self.w2, hidden))
        return out, hidden

    def learn(self, item: Item, *, gated: bool) -> float:
        """한 항목 1스텝.

        ``gated=True`` 면 학습률에 검증 강도를 곱한다 — 검증 안 된 지식은
        가중치를 거의 못 움직인다. 이것이 "언제 어디를 말랑하게" 게이팅의
        가장 조악한 1차 근사다 (국소성 없음 — 전역 스칼라일 뿐이다).
        """
        out, hidden = self.forward(item.inputs)
        error = out - item.target
        lr = self.lr * (item.verification if gated else 1.0)

        grad_hidden = [self.w2[j] * error for j in range(len(hidden))]
        for j in range(len(hidden)):
            self.w2[j] -= lr * error * hidden[j]
        for j, row in enumerate(self.w1):
            d = grad_hidden[j] * (1.0 - hidden[j] ** 2)
            for i in range(len(row)):
                row[i] -= lr * d * item.inputs[i]
        return error**2

    def loss_on(self, items: list[Item]) -> float:
        if not items:
            return 0.0
        return sum((self.forward(i.inputs)[0] - i.target) ** 2 for i in items) / len(items)


#: 두 태스크는 **서로 다른 입출력 대응**이어야 한다. 같은 함수를 두 번 배우면
#: 간섭이 안 일어나고, 그러면 이 하네스는 아무것도 보여주지 못한다.
def target_a(x: list[float]) -> float:
    """태스크 A: 합에 대한 단조 반응."""
    return math.tanh((x[0] + x[1] + x[2] + x[3]) / 2)


def target_b(x: list[float]) -> float:
    """태스크 B: 곱(상호작용) 중심 — A 와 다른 특징을 요구한다."""
    return math.tanh(2.0 * x[0] * x[1] - x[2] + 0.5 * x[3])


def make_items(
    rng: random.Random, n: int, verification: float, target, prefix: str
) -> list[Item]:
    items = []
    for k in range(n):
        inputs = [rng.uniform(-1, 1) for _ in range(4)]
        items.append(Item(f"{prefix}{k}", inputs, target(inputs), verification))
    return items


def run(gated: bool, steps: int = 400) -> dict[str, float]:
    """태스크 A 를 배운 뒤 태스크 B 를 **순차로** 배운다. A 가 얼마나 남는가."""
    rng = random.Random(SEED)
    net = TinyNet(n_in=4)

    # 검증된 옛 지식 (= 보호 대상 자기) / 미검증 새 지식 (= 후보)
    task_a = make_items(rng, 16, 1.0, target_a, "a")
    task_b = make_items(rng, 16, 0.15, target_b, "b")

    for _ in range(steps):
        for item in task_a:
            net.learn(item, gated=gated)
    loss_a_before = net.loss_on(task_a)

    for _ in range(steps):
        for item in task_b:
            net.learn(item, gated=gated)

    return {
        "loss_a_before": loss_a_before,
        "loss_a_after": net.loss_on(task_a),
        "loss_b_after": net.loss_on(task_b),
    }


def forgetting(result: dict[str, float]) -> float:
    """B 를 배우느라 A 에서 잃은 양. 클수록 망각이 심하다."""
    return result["loss_a_after"] - result["loss_a_before"]


def main() -> None:
    print("승격 (b)경로 실험 — 순차 학습에서 게이팅의 효과\n")
    print(f"{'모드':<12}{'A 학습 후':>12}{'B 학습 후 A':>14}{'망각량':>12}{'B 최종':>12}")
    print("-" * 62)
    for gated in (False, True):
        r = run(gated)
        label = "게이팅 있음" if gated else "게이팅 없음"
        print(
            f"{label:<12}{r['loss_a_before']:>12.5f}"
            f"{r['loss_a_after']:>14.5f}{forgetting(r):>12.5f}"
            f"{r['loss_b_after']:>12.5f}"
        )
    print(
        "\n읽는 법: '게이팅 없음'에서 A 의 손실이 B 학습 후 올라가면 파국적 망각의\n"
        "재현이다. '게이팅 있음'은 미검증 지식(B)의 학습률을 눌러 A 를 지키지만,\n"
        "그만큼 B 를 못 배운다 — 안정성-가소성 딜레마를 **푼 것이 아니라 손잡이를\n"
        "어디에 둘지 고른 것**이다 (세션 1: 완화책은 전제를 약화시킬 뿐이다).\n"
        "진짜 표적은 여전히 미해결: 전역 경사하강 없이 검증 노드 하나를 통합하는\n"
        "최소 메커니즘 (spec §8)."
    )


if __name__ == "__main__":
    main()
