# CLAUDE.md — coral 레포 작업 지침

## 이 프로젝트가 무엇인가 (30초 버전)
인간-AI 대화의 지식을 구조로 포획(P→I→C)하고, 시장으로 검증(스테이킹·
실측 닻)하고, 신경망으로 체화하는 **살아있는 집단 학습 시스템**.
은유: 산호초 — 동결 기저 LLM = 기존 안정층, 우리 시스템 = 폴립층(활성면),
검증 통과 지식이 안정층으로 **승격**되며 초가 자란다.

## 필독 순서 (코드 작성 전 반드시)
1. `docs/plasticity-layer-spec.md` — 설계 정본 v0.2 (아키텍처·원칙·미해결)
2. `docs/full-discussion-record.md` — 논증 상세 (왜 이 설계인가, 기각된
   대안 포함)
3. `docs/session-log-2026-08.md` — 압축 연대기
4. `docs/verification-protocol.md` — 사용자와의 소통 규약

## 구현 대상: MVP "최소 한 바퀴"
사용자 질문 → 기저 LLM 답변 → P→I→C 구조화 → 사용자 승인/증분 →
스테이킹/검증 → 승격 판정 → 온톨로지 정본에 굳음 → 다음 질문에서
굳은 지식이 참조됨(컨텍스트 주입).

## 레포 구조
```
/docs    정본 4문서 (수정 시 사용자 합의 필수)
/app     서비스 트랙 (MVP 한 바퀴)
/lab     가소성 실험 트랙 (승격 (b)경로 연구 — /app과 격리)
```

## 기존 자산 재사용 (새로 만들지 말 것)
- `github.com/wilcoco/CAMS-KnowledgeNet` (branch:
  claude/ontology-knowledge-evaluation-uzuDx) — src/nightwish/의
  scoring.py(허브/권위·안목 보상), economy.py(닫힌 포인트·스테이킹·배당),
  verification.py(외부 현실 닻), governance.py, tree.py(잠복/부활).
  의존성 0으로 설계됨 — 클론 후 필요 모듈 이식.
- `github.com/wilcoco/H2A2H2` — P→I→C 노드/엣지 스키마 (node: concept·
  claim·evidence·source·qa·premise·inference·conclusion / edge: supports·
  refutes·relates_to·cites·infers) — 포맷 호환 유지.

## 설계 원칙 (위반 금지)
- 기저 LLM은 **산출물 층위**로만 접합 (텍스트 in/out). 내부 표현 개입
  금지 — 기저 교체 가능성이 전략 자산.
- 승격 판정 기준은 novelty가 아니라 **손상** (기존 검증 지식과의 충돌 시험).
- 노드는 삭제·덮어쓰기 대신 **잠복(dormant)** — 부활 가능해야 함.
- 가격/보상 신호는 대리변수(조회수류) 금지 — 스테이킹 + 사후 실측만.
- /lab 실험 코드가 /app을 오염시키지 않게 격리.

## 사용자 소통 규약 (verification-protocol 준수)
- 주요 주장에 "문헌 합의 / 내 보간" 구분 명시. 확신도 표기.
- 사용자 아이디어를 기존 범주로 분류할 때 차이점 반드시 병기.
- Claude 중력 4건 전례 있음 (full-record §E2) — 스코프 축소·기존 패러다임
  회귀 방향의 제안은 스스로 의심할 것.
- 설계 변경은 이 대화(채팅 세미나)와 합의 후 docs에 반영.

## 미해결 (건드리되 풀렸다고 주장하지 말 것)
손상 판정 함수 · 음성 선택 보수성(쿤 문제) · 관문 적응 재귀 · 자기의
계산적 정의 · 암묵지 병목 · 승격 (b)경로(가소성 본체). 상세: spec §6.

## 구현 현황 (2026-08-20, 관문 v1.3 + 검색 회로)
- **관문 판정권은 사람과 현실에만 있다.** `judge()` 인자에 LLM 통로가 없고,
  기저는 제보자(`make_advisor` → `Advisory` 타입)로 강등됐다. 이 구조를
  `test_judge_has_no_channel_for_an_llm` / `test_gate_never_calls_the_llm` 이
  강제한다 — **되돌리지 말 것 (중력 5)**.
- 사용자 화면 `/` (app.html, 기계 어휘 금지) / 운영자 콘솔 `/console` (3-패널).
- 관문 지표는 누적 인정(권위)이고 **링크 가중치는 1.0 고정** — 액수로 관문
  가중치를 사지 못하게. 문턱 단위는 포인트가 아니라 인정 건수(기본 3).
- 설계 문서: `docs/promotion-gate-spec.md`(v1.3) · `docs/reference-repos.md` ·
  `docs/palantir-analogy.md`.

## 구현 현황 (2026-08-19, MVP 한 바퀴 완료)
- `/app` — 한 바퀴 동작함. 진입점 `app/web/app.py`, 오케스트레이션은
  `app/store/service.py`. 설계 → 코드 대응표는 `docs/mvp-one-wheel.md`.
- `/lab` — 승격 (b)경로 실험 하네스만 있음 (`promotion_b_sketch.py`). 답 아님.
- `/tests` — 50개. `test_one_wheel.py::test_the_wheel_closes` 가 바퀴가 닫히는지
  (승격된 지식이 다음 문답에 주입되는지) 직접 검사한다.
- 배포: Railway (`railway.json`), 문서는 `docs/deploy-railway.md`.
- **코드를 고치기 전에 `docs/mvp-one-wheel.md` §3(의도적으로 축소한 것)을 읽을 것.**
  거기 적힌 것들은 미구현이지 미완성이 아니다 — "마저 구현"하지 말고 세미나 합의를
  거칠 것.
