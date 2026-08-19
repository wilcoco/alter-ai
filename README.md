# 🪸 coral

> 인간-AI 대화에서 태어나는 지식을 **구조로 포획**하고, **시장으로 검증**하고,
> **신경망으로 체화**시키는 살아있는 집단 학습 시스템.

동결 기저 LLM = 인류 문헌이 석회화된 **기존 안정층**. 우리 시스템 = 그 죽은 초
표면에 붙는 **폴립층**. 검증을 통과한 지식이 **안정층으로 승격**되며 초가 자란다.

**스케일은 남의 전쟁, 우리는 그 위에 누적되는 것을 소유한다.**

---

## 지금 이 레포에 있는 것: MVP "최소 한 바퀴"

```
질문 ──▶ [정본 주입] ──▶ 기저 LLM 답변 ──▶ P→I→C 구조화 ──▶ 사용자 승인/증분
                                                                  │
   ┌──────────────────────────────────────────────────────────────┘
   ▼
스테이킹 · 외부 실측 닻 ──▶ 승격 판정(손상 시험) ──▶ 정본에 굳음 ──┐
                                                                  │
   ┌──────────────────────────────────────────────────────────────┘
   ▼
다음 질문에 주입 ──▶ 바퀴가 닫힌다
```

마지막 화살표가 이 MVP 가 증명하는 전부다. 승격된 지식이 다음 문답에 실제로
참조되지 않으면 승격은 장부상의 사건일 뿐이다.
`tests/test_one_wheel.py::test_the_wheel_closes` 가 그 화살표를 직접 검사한다.

구현된 것은 **승격 (a)경로**(온톨로지 정본에 굳음)이다. (b)경로(상부망 가중치
체화)는 미해결이며 `/lab` 에서 격리된 채 연구한다.

## 빠른 시작

```bash
pip install -e ".[dev]"
uvicorn app.web.app:app --reload     # http://127.0.0.1:8000
pytest
```

`ANTHROPIC_API_KEY` 가 없어도 뜬다 — 기저가 stub 로 떨어질 뿐 한 바퀴의 구조는
그대로 돈다. (기저 독립성의 실측이기도 하다.)

배포: [`docs/deploy-railway.md`](docs/deploy-railway.md)

## 레포 구조

```
/docs   정본 4문서 (수정 시 사용자 합의 필수) + 구현 매핑·배포 문서
/app    서비스 트랙 — MVP 한 바퀴
/lab    가소성 실험 트랙 (승격 (b)경로 연구 — /app 과 격리)
/tests  불변식 + 한 바퀴 통합 테스트
```

`/app` 은 `/lab` 을 절대 import 하지 않고, `app/core` 는 프레임워크를 import
하지 않는다 (의존성 0). 둘 다 `tests/test_isolation.py` 가 강제한다.

## 기존 자산 재사용

| 출처 | 가져온 것 |
|---|---|
| [CAMS-KnowledgeNet](https://github.com/wilcoco/CAMS-KnowledgeNet) | `scoring.py`(허브/권위·안목 보상) · `economy.py`(닫힌 포인트·스테이킹·배당) · `verification.py`(외부 현실 닻) — 의존성 0 으로 설계되어 그대로 이식 |
| [H2A2H2](https://github.com/wilcoco/H2A2H2) | P→I→C 노드/엣지 스키마 (포맷 호환 유지) · 3-패널 UI 구성 |

## 설계 원칙 (코드가 지키는 것)

- 기저 LLM 은 **산출물 층위**로만 접합한다 (텍스트 in/out). 내부 표현 개입 금지 —
  기저 교체 가능성이 전략 자산이다. → `app/capture/llm.py` 가 얇은 이유.
- 승격 판정 기준은 novelty 가 아니라 **손상**이다 (danger model). → `app/core/promotion.py`
- 노드는 삭제·덮어쓰기 대신 **잠복(dormant)** — 부활 가능해야 한다.
- 가격/보상 신호는 대리변수(조회수류) 금지 — **스테이킹 + 사후 실측**만.
- `/lab` 실험 코드가 `/app` 을 오염시키지 않게 격리.

## 미해결 (건드리되 풀렸다고 주장하지 말 것)

손상 판정 함수 · 음성 선택 보수성(쿤 문제) · 관문 적응 재귀 · 자기의 계산적 정의 ·
암묵지 병목 · 승격 (b)경로(가소성 본체).
상세: [`docs/plasticity-layer-spec.md`](docs/plasticity-layer-spec.md) §6,
구현이 어디까지 갔는지: [`docs/mvp-one-wheel.md`](docs/mvp-one-wheel.md) §미해결.

## 읽는 순서

1. [`docs/plasticity-layer-spec.md`](docs/plasticity-layer-spec.md) — 설계 정본 v0.2
2. [`docs/full-discussion-record.md`](docs/full-discussion-record.md) — 논증 상세 (기각된 대안 포함)
3. [`docs/session-log-2026-08.md`](docs/session-log-2026-08.md) — 압축 연대기
4. [`docs/verification-protocol.md`](docs/verification-protocol.md) — 소통 규약
5. [`docs/mvp-one-wheel.md`](docs/mvp-one-wheel.md) — 설계 → 코드 대응표
