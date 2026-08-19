"""coral — 살아있는 집단 학습 시스템 (MVP "최소 한 바퀴").

레포 구조 규약 (CLAUDE.md):
  /docs   정본 4문서
  /app    서비스 트랙 — 이 패키지
  /lab    가소성 실험 트랙 (승격 (b)경로 연구, /app 과 격리)

/app 은 /lab 을 절대 import 하지 않는다. 격리는 tests/test_isolation.py 가 강제한다.
"""

__version__ = "0.1.0"
