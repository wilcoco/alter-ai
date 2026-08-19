# Railway 배포

## 한 번만 하면 되는 것

1. **Railway → New Project → Deploy from GitHub repo** → 이 레포 선택,
   브랜치는 `claude/mvp-build-deploy-zuf8tv`.
2. Nixpacks 가 `requirements.txt` 를 읽어 자동으로 빌드한다. 시작 명령·헬스체크는
   `railway.json` 에 들어 있으므로 따로 설정할 필요가 없다.
3. **Settings → Networking → Generate Domain** 으로 공개 URL 을 만든다.

이 상태로 이미 뜬다. 아래 두 가지는 붙이면 좋아지는 것이지 필수가 아니다.

## 기저 LLM 붙이기

`ANTHROPIC_API_KEY` 를 Variables 에 넣는다. 없으면 앱은 **stub 모드**로 뜬다 —
한 바퀴의 구조(포획 → 검증 → 승격 → 주입)는 그대로 돌고 기저 답변만 대체된다.
헤더와 `/api/health` 의 `llm` 필드로 현재 모드를 확인할 수 있다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | (없음) | 없으면 stub 기저 |
| `CORAL_BASE_MODEL` | `claude-opus-5` | 기저는 교체 가능한 부품이다 |
| `CORAL_MAX_TOKENS` | `8000` | |
| `CORAL_PROMOTION_STAKE_THRESHOLD` | `25` | 승격에 필요한 최소 누적 스테이크 |
| `CORAL_LARGE_STAKE_THRESHOLD` | `25` | 이 이상 스테이크는 기여 동반 필수 |
| `CORAL_QUARANTINE_TICKS` | `1` | 잠복기 (관문 심사 통과 횟수) |
| `CORAL_UBI_GRANT` | `100` | 신규 참여자 UBI |
| `CORAL_INJECTION_TOP_K` | `8` | 다음 질문에 주입할 정본 노드 최대 개수 |

## 데이터 영속

기본은 SQLite (`./data/coral.db`). **Railway 의 파일시스템은 재배포마다
초기화되므로, 지식을 쌓으려면 둘 중 하나가 필요하다:**

- **Postgres 추가 (권장)** — 프로젝트에 `+ New → Database → PostgreSQL` 을 붙이면
  `DATABASE_URL` 이 자동 주입되고 앱이 그걸 집어 쓴다. `postgres://` 접두사는
  코드에서 SQLAlchemy 드라이버 URL 로 정규화한다.
- **Volume 마운트** — Volume 을 만들어 `/data` 에 붙이고 `CORAL_DATA_DIR=/data`
  를 설정하면 SQLite 파일이 살아남는다.

## 확인

```bash
curl https://<your-domain>/api/health
# {"status":"ok","version":"0.1.0","llm":"claude-opus-5","llm_enabled":true,"store":"postgresql"}
```

헬스체크는 DB 왕복(`SELECT 1`)까지 하므로, 앱만 뜨고 DB 가 죽은 상태를 잡아낸다.

## 로컬에서 돌리기

```bash
pip install -e ".[dev]"
uvicorn app.web.app:app --reload
# http://127.0.0.1:8000
pytest
```
