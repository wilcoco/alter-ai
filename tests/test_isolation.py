"""격리 규약 강제 — CLAUDE.md "위반 금지".

두 가지를 지킨다:

1. **/app 은 /lab 을 import 하지 않는다.** 실험 코드가 서비스 트랙을 오염시키면
   안 된다. (반대 방향 — lab → app — 은 허용이다.)
2. **app/core 는 프레임워크를 import 하지 않는다.** 도메인 코어의 의존성 0
   설계(KnowledgeNet 에서 이식할 때 지킨 성질)를 잃지 않기 위해서다.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 도메인 코어가 절대 끌어와서는 안 되는 것들
FORBIDDEN_IN_CORE = {"fastapi", "sqlalchemy", "anthropic", "pydantic", "starlette"}


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _app_files() -> list[pathlib.Path]:
    return sorted((ROOT / "app").rglob("*.py"))


def test_app_never_imports_lab():
    offenders = []
    for path in _app_files():
        for module in _imported_modules(path):
            if module == "lab" or module.startswith("lab."):
                offenders.append(f"{path.relative_to(ROOT)} -> {module}")
    assert not offenders, (
        "/app 이 /lab 을 import 했다 — 실험 트랙이 서비스 트랙을 오염시킨다:\n"
        + "\n".join(offenders)
    )


def test_core_has_zero_framework_dependencies():
    offenders = []
    for path in sorted((ROOT / "app" / "core").rglob("*.py")):
        for module in _imported_modules(path):
            root = module.split(".")[0]
            if root in FORBIDDEN_IN_CORE:
                offenders.append(f"{path.relative_to(ROOT)} -> {module}")
    assert not offenders, (
        "app/core 는 의존성 0 이어야 한다 (메커니즘을 프레임워크 없이 읽고 "
        "테스트할 수 있게):\n" + "\n".join(offenders)
    )


def test_core_only_imports_core():
    """코어는 app 의 다른 층(store·web·capture)도 끌어오지 않는다."""
    offenders = []
    for path in sorted((ROOT / "app" / "core").rglob("*.py")):
        for module in _imported_modules(path):
            if module.startswith("app.") and not module.startswith("app.core"):
                offenders.append(f"{path.relative_to(ROOT)} -> {module}")
    assert not offenders, "app/core 가 상위 층을 import 했다:\n" + "\n".join(offenders)
