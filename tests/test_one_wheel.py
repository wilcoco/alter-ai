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


def test_user_page_has_no_machine_vocabulary(client):
    """§2 언어 규약 — 기계 어휘가 사용자 화면에 새면 버그다."""
    page = client.get("/")
    assert page.status_code == 200
    for machine_word in ["폴립", "석회화", "신규 안정층", "스테이킹", "정본"]:
        assert machine_word not in page.text, f"기계 어휘 노출: {machine_word}"
    for user_word in ["검증됨", "검토 중", "틀렸어요", "보탤게요"]:
        assert user_word in page.text


def test_operator_console_keeps_the_machine_view(client):
    page = client.get("/console")
    assert page.status_code == 200
    assert "폴립층" in page.text and "신규 안정층" in page.text


# ---------------------------------------------------------------------------
# 검색 선점 · 사람 반증 · 재심 캐스케이드 (v1.3)
# ---------------------------------------------------------------------------

def test_search_precedes_the_llm_and_exposes_polyps(client):
    """H1·H2 — 검색은 LLM 을 부르지 않고, 폴립도 심사대에 올린다."""
    asked = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()
    assert asked["node_ids"]

    found = client.get("/api/search", params={"q": "웰드라인 경화제 점도"}).json()
    assert found["results"], "검색이 아무것도 못 찾았다 — 노출면이 비면 심사대도 빈다"
    # 아직 승격된 게 없으므로 폴립이 노출돼야 한다
    assert any(r["status"] == "polyp" for r in found["results"])
    # 각 결과는 판단에 필요한 상태를 동봉한다
    first = found["results"][0]
    for key in ("verified", "challenged", "recognition", "explore_slot"):
        assert key in first


def test_refutation_creates_a_sibling_and_never_edits_the_original(client):
    """반증은 원본을 고치지 않는다 — 형제 노드 + refutes 엣지."""
    asked = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()
    target = asked["node_ids"][0]
    before = client.get("/api/search", params={"q": QUESTION}).json()
    original = next(r for r in before["results"] if r["id"] == target)

    out = client.post(
        f"/api/nodes/{target}/refute",
        json={
            "author": "bob",
            "reason": "재현 안 됨",
            "claim": "8000rpm 이상에서는 반대 결과가 나온다",
            "stake": 15,
        },
    ).json()
    assert out["refuter_id"] != target

    after = client.get("/api/search", params={"q": QUESTION}).json()
    still = next(r for r in after["results"] if r["id"] == target)
    assert still["title"] == original["title"]      # 원본 불변
    assert still["challenged"]                      # ⚠ 가 동행한다
    assert still["challenges"][0]["author"] == "bob"


def test_refutation_below_the_minimum_stake_is_rejected(client):
    """입증 책임 — 공짜 거부권은 없다."""
    asked = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()
    res = client.post(
        f"/api/nodes/{asked['node_ids'][0]}/refute",
        json={"author": "bob", "reason": "사실 오류", "claim": "아니다", "stake": 1},
    )
    assert res.status_code == 400
    assert "입증 책임" in res.json()["error"]


def test_pending_refutation_does_not_block_promotion(client):
    """계류 반증은 차단하지 않고 동행한다 (spec §5)."""
    asked = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()
    target = asked["node_ids"][0]
    client.post(
        f"/api/nodes/{target}/refute",
        json={"author": "bob", "reason": "조건 누락", "claim": "예외가 있다", "stake": 15},
    )
    client.post(
        f"/api/nodes/{target}/measure",
        json={
            "metric": "웰드라인 불량률",
            "baseline": 8.0,
            "observed": 2.0,
            "direction": "lower_better",
        },
    )
    decision = client.post(f"/api/nodes/{target}/promote").json()
    assert decision["verdict"] == "promote"
    assert decision["challenged"] is True      # 차단하지 않되 기록된다


def test_verified_refutation_cascades_and_demotes_its_target(client):
    """반증이 검증을 통과하면 대상이 즉시 재심돼 밀려난다 — 정본도 예외 없음."""
    asked = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()
    target = asked["node_ids"][0]
    client.post(
        f"/api/nodes/{target}/measure",
        json={"metric": "불량률", "baseline": 8.0, "observed": 2.0,
              "direction": "lower_better"},
    )
    assert client.post(f"/api/nodes/{target}/promote").json()["verdict"] == "promote"

    refuted = client.post(
        f"/api/nodes/{target}/refute",
        json={"author": "bob", "reason": "재현 안 됨",
              "claim": "고속 조건에서 반대 결과", "stake": 15},
    ).json()
    refuter = refuted["refuter_id"]
    # 반증 자체를 실측으로 세운다 — "반증했다"가 아니라 "반증이 검증됐다"가 손상
    client.post(
        f"/api/nodes/{refuter}/measure",
        json={"metric": "고속 불량률", "baseline": 9.0, "observed": 3.0,
              "direction": "lower_better"},
    )
    out = client.post(f"/api/nodes/{refuter}/promote").json()
    assert out["verdict"] == "promote"
    assert "cascade" in out, "반증 승격이 대상을 재심시키지 않았다"
    assert out["cascade"][0]["verdict"] == "damage"

    state = client.get("/api/state").json()
    assert target in [n["id"] for n in state["dormant"]]


def test_gate_never_calls_the_llm(client, monkeypatch):
    """중력 5 재발 방지 — 승격 경로에서 기저 LLM 이 호출되면 실패한다."""
    from app.capture import llm as llm_mod

    asked = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()

    called = []
    original = llm_mod.get_llm

    def tripwire():
        called.append(True)
        return original()

    monkeypatch.setattr("app.store.service.get_llm", tripwire)
    client.post("/api/nodes/" + asked["node_ids"][0] + "/promote")
    client.post("/api/promote-all")
    assert not called, "관문이 기저 LLM 을 호출했다 — 판정권이 새고 있다"


def test_my_activity_shows_reuse_of_my_knowledge(client):
    """H3 측정면 — 내 지식이 쓰인 횟수가 보여야 기여가 반복된다."""
    asked = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()
    node_id = asked["node_ids"][0]
    client.post(
        f"/api/nodes/{node_id}/measure",
        json={"metric": "불량률", "baseline": 8.0, "observed": 2.0,
              "direction": "lower_better"},
    )
    client.post(f"/api/nodes/{node_id}/promote")
    client.post("/api/ask", json={"question": FOLLOW_UP, "author": "carol"})

    mine = client.get("/api/me", params={"account": "alice"}).json()
    assert mine["reused"] >= 1
    assert any(c["reused"] >= 1 for c in mine["contributions"])


def test_stub_non_answers_do_not_pollute_the_corpus(client):
    """기저가 "답이 없다"고 말한 것을 지식으로 포획하면 안 된다.

    실사용 화면 캡처에서 발견된 오염 — stub 경고문이 주장 노드가 되어 검색
    결과를 채웠다. 문답 자체(질문)는 기록되되 주장 노드는 생기지 않아야 한다.
    """
    asked = client.post(
        "/api/ask", json={"question": QUESTION, "author": "alice"}
    ).json()
    assert asked["stubbed"], "이 테스트는 stub 기저를 전제한다"
    assert len(asked["node_ids"]) == 1, "stub 답변에서 주장 노드가 생성됐다"

    found = client.get("/api/search", params={"q": QUESTION}).json()
    for r in found["results"]:
        assert "stub" not in r["title"].lower(), f"stub 경고문이 검색에 노출: {r['title']}"
