"""통합 — MVP 가 증명해야 하는 단 한 가지: **바퀴가 닫히는가.**

    질문 → 기저 답 → P→I→C 구조화 → 승인/증분 → 스테이킹/검증 →
    승격 판정 → 정본에 굳음 → **다음 질문에서 그 지식이 참조됨**

마지막 화살표가 없으면 승격은 장부상의 사건일 뿐이고 시스템은 아무것도
학습하지 않은 것이다. 그 화살표를 직접 검사한다.
"""

from __future__ import annotations

QUESTION = "웰드라인 불량을 줄이려면 경화제 점도를 어떻게 잡아야 하나?"
FOLLOW_UP = "경화제 점도와 웰드라인 불량의 관계를 다시 정리해줘"


def test_the_wheel_closes(client):
    # 1. 질문 → 기저 답 → 포획. 정본이 비었으므로 주입은 없다.
    first = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()
    assert first["injected_ids"] == []
    assert first["node_ids"], "포획된 폴립이 없다 — 지식이 증발했다"

    node_id = first["node_ids"][0]

    # 2. 사용자 증분 (문답의 소유자는 사용자다)
    responded = client.post(
        f"/api/turns/{first['turn_id']}/respond",
        json={
            "accept": True,
            "increment": "점도를 낮추면 합류부 온도가 유지되어 불량이 준다",
            "author": "alice",
        },
    ).json()
    assert responded["increment_node_id"]

    # 3. 외부 현실 닻 — 동의의 순환 밖에 있는 유일한 신호
    measured = client.post(
        f"/api/nodes/{node_id}/measure",
        json={
            "metric": "웰드라인 불량률",
            "baseline": 8.0,
            "observed": 2.0,
            "direction": "lower_better",
            "min_rel_improvement": 0.2,
            "reporter": "bob",
        },
    ).json()
    assert measured["passes"]

    # 4. 승격 판정 → 정본에 굳음
    promoted = client.post(f"/api/nodes/{node_id}/promote").json()
    assert promoted["verdict"] == "promote", promoted["reason"]
    assert promoted["status"] == "canonical"

    state = client.get("/api/state").json()
    assert node_id in [n["id"] for n in state["canonical"]]

    # 5. **바퀴가 닫힌다** — 다음 질문의 답에 굳은 지식이 주입된다
    second = client.post(
        "/api/ask", json={"question": FOLLOW_UP, "author": "carol"}
    ).json()
    assert node_id in second["injected_ids"], (
        "승격된 지식이 다음 문답에 주입되지 않았다 — 바퀴가 닫히지 않는다"
    )


def test_staking_moves_points_and_enforces_the_contribution_rule(client):
    asked = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()
    node_id = asked["node_ids"][0]

    staked = client.post(
        f"/api/nodes/{node_id}/stake", json={"account": "bob", "amount": 30.0}
    ).json()
    assert staked["total_staked"] == 30.0
    assert staked["balance"] == 70.0        # UBI 100 에서 30 이 잠겼다

    # 잔액을 넘는 스테이크는 규칙 위반 — 400 으로 사용자에게 그대로 보인다
    over = client.post(
        f"/api/nodes/{node_id}/stake", json={"account": "bob", "amount": 5000.0}
    )
    assert over.status_code == 400
    assert "error" in over.json()


def test_unverified_knowledge_is_not_injected(client):
    """폴립은 주입되지 않는다 — 굳은 것만 다음 문답에 참조된다."""
    asked = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()
    assert asked["node_ids"]

    again = client.post(
        "/api/ask", json={"question": FOLLOW_UP, "author": "alice"}
    ).json()
    assert again["injected_ids"] == [], "승격되지 않은 폴립이 주입됐다"


def test_promotion_holds_until_evidence_accumulates(client):
    """근거 없는 후보는 기각되지 않고 보류된다 (폴립층 유지)."""
    asked = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()
    node_id = asked["node_ids"][0]

    held = client.post(f"/api/nodes/{node_id}/promote").json()
    assert held["verdict"] == "hold"
    assert held["status"] == "polyp"

    state = client.get("/api/state").json()
    assert state["stats"]["canonical"] == 0
    assert state["stats"]["polyp"] >= 1


def test_health_endpoint_reports_llm_and_store(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["llm"] == "stub"          # 테스트는 키 없이 돈다
    assert health["store"] == "sqlite"


def test_index_page_renders(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "폴립층" in page.text and "신규 안정층" in page.text
