# MVP "최소 한 바퀴" — 설계 → 코드 대응표

> **이 문서는 정본이 아니라 구현 기록이다.** 정본 4문서(spec · full-record ·
> session-log · protocol)는 이 구현 때문에 바뀌지 않았다. 설계 변경은 채팅
> 세미나 합의를 거쳐야 하며, 여기서는 **설계의 어느 부분이 코드가 되었고 어느
> 부분이 되지 않았는지**만 기록한다.

## 1. 한 바퀴의 각 단계가 어디에 있나

| 단계 | 설계 근거 | 코드 | API |
|---|---|---|---|
| 정본 주입 | spec §1 승격 (a)경로의 환류 | `app/injection.py` | (ask 내부) |
| 기저 LLM 답변 | spec §3 산출물 층위 접합 | `app/capture/llm.py` | `POST /api/ask` |
| P→I→C 구조화 | spec §4 · H2A2H2 ADR-0001 | `app/capture/pic.py` | `POST /api/ask` |
| 사용자 승인/증분 | §C3 "문답의 소유자는 사용자" | `service.respond` | `POST /api/turns/{id}/respond` |
| 스테이킹 | §B6 지식 시장 · economy.py | `app/core/economy.py` | `POST /api/nodes/{id}/stake` |
| 외부 현실 닻 | §C1 verification.py | `app/core/verification.py` | `POST /api/nodes/{id}/measure` |
| 승격 판정 (손상) | §B4 면역계 · §7 danger model | `app/core/promotion.py` | `POST /api/nodes/{id}/promote` |
| 정본에 굳음 | spec §1 신규 안정층 | `Ontology.promote` | — |
| 잠복 / 부활 | §C1 tree.py 죽지 않는 가지 | `Ontology.make_dormant/revive` | `POST /api/nodes/{id}/revive` |

## 2. 설계 원칙이 코드에서 어떻게 강제되나

- **기저 독립성** — `app/capture/llm.py` 의 인터페이스는 `answer` 와 `extract`
  둘뿐이다. 이 파일이 두꺼워지면 기저에 종속된 것이다. `ANTHROPIC_API_KEY` 없이
  전체 테스트가 통과하는 것이 독립성의 실측이다.
- **손상 ≠ novelty** — `tests/test_promotion.py::test_novelty_alone_never_blocks_promotion`
  과 `::test_damage_rejects_regardless_of_stake` 가 양방향으로 못 박는다.
  손상 프로브의 프롬프트(`app/capture/damage.py`)도 "정본에 없다는 이유로 절대
  conflict 를 만들지 마라"를 명시한다.
- **삭제 없음** — 기각은 `DORMANT` 이고 노드는 그래프에 남는다. 잠긴 포인트는
  소각이 아니라 유동성 풀로 환원되어 부활 시 원 스테이커에게 복원된다.
- **대리변수 금지** — 조회수·좋아요류 필드가 스키마에 아예 없다. 가격 신호는
  스테이크와 `Measurement` 뿐이다.
- **격리** — `tests/test_isolation.py` 가 `/app → /lab` import 와
  `app/core` 의 프레임워크 import 를 AST 로 금지한다.

## 3. 설계에서 왔지만 **의도적으로 축소**한 것

정직하게 적는다. 아래는 "구현했다"고 말하면 안 되는 것들이다.

- **손상 판정 함수** — v0. 구조적 신호(정본의 `refutes` 엣지)와 의미적 신호
  (기저 LLM 모순 시험) 둘뿐이다. 후자는 **관문이 기저의 판단에 의존하는 순환**을
  품고 있다 (spec §6-3). 외부 현실 닻만이 이 순환 밖에 있고, 그래서 실측이
  통과하면 스테이크 문턱을 면제하도록 정책이 짜여 있다.
- **음성 선택의 보수성(쿤 문제)** — 안 풀었다. 완충으로 `CONFIDENCE_FLOOR`
  (낮은 확신의 충돌은 손상으로 안 셈)를 두었을 뿐이다. 패러다임 전환 지식이
  정의상 정본과 충돌한다는 구조적 문제는 그대로 남아 있다.
- **자기(self)의 계산적 정의** — "자기 = 승격된 정본 온톨로지"로 **부분** 답만
  썼다. 능력 차원의 자기는 정의되지 않았다.
- **라우팅** (spec §3 남은 문제) — 기저 답 채택 vs 상부 개입의 판별 함수는
  구현하지 않았다. 사용자의 채택/증분 여부를 `Turn.accepted` / `Turn.increment`
  에 **기록만** 해 둔다. 판별 함수를 짤 원시 신호를 모으는 단계다.
- **검색 병목** — 주입 검색은 토큰 겹침 + 권위 가중이다. 임베딩을 안 쓴 것은
  RAG 의 구조적 한계(§C1: "검색기는 모델만큼 관련성을 이해하지 못한다")가
  임베딩으로도 안 풀리기 때문이며, 이 한계는 (b)경로의 몫으로 남긴다.
- **암묵지 병목** — 안 풀었다. `pic.py` 는 명제로 담기지 않는 것을
  `unresolved` 로 뽑아 **기록**만 한다. 담기지 않는다는 사실 자체를 보존하려는
  것이지 담아낸 것이 아니다.
- **잠복기** — 벽시계 시간이 아니라 **관문 심사를 통과한 횟수**로 셌다.
  "N일 개인층 운용 후 통합 심사"(§B4)의 축소판이다.
- **승격 (b)·(c)경로** — (b)는 `/lab` 에 실험 하네스만 있고 (c)는 착수 안 했다.
- **거버넌스** — KnowledgeNet 의 `governance.py`(N명 시 자동 분권)는 이식하지
  않았다. MVP 참여자 규모에서 검증할 수 없는 것을 코드로 들이지 않았다.

## 4. 다음 표적

1. **라우팅 판별 함수** — 쌓인 `accepted`/`increment` 신호로 "기저 답이 충분한
   질문"과 "상부 개입이 필요한 질문"을 가르는 실측 가능한 함수.
2. **정산 역류** (spec §8) — "통합 후 성능 기여"를 측정해 배당을 사후 보정하는
   설계. 지금은 스테이킹 시점에만 배당이 돈다.
3. **손상 판정의 순환 끊기** — 관문이 기저에 의존하지 않는 신호원.
4. **승격 (b)경로** — 세미나 진행에 따라 `/lab` 에서.
