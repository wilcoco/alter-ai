"""포획 층 — 대화에서 태어나는 지식을 구조로 붙잡는다 (파이프라인 최상류).

``llm``     기저 LLM 어댑터 (산출물 층위 접합만 — spec §3)
``pic``     P→I→C 추론 구조 포획 (H2A2H2 스키마 호환)
``damage``  의미적 손상 프로브 (석회화 판정 기관에 주입)
"""

from app.capture.damage import make_advisor
from app.capture.llm import Answer, BaseLLM, StubLLM, get_llm
from app.capture.pic import PICGraph, capture

__all__ = [
    "Answer",
    "BaseLLM",
    "PICGraph",
    "StubLLM",
    "capture",
    "get_llm",
    "make_advisor",
]
