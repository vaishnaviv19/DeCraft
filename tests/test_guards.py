# tests/test_guards.py  -- architectural guard tests (M0.6)
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
MODULES = {"inference", "privacy", "generation", "validation", "narrative", "rendering", "llm", "extraction"}
EXEMPT = {"blueprint", "planner"}            # existing code is not policed by these guards


def py_files():
    return [p for p in SRC.rglob("*.py") if p.relative_to(SRC).parts[0] not in EXEMPT]


def test_no_eval_exec_compile():
    """AST based: comments and docstrings may mention eval(); only real calls fail. re.compile is allowed."""
    import ast
    bad = []
    for p in py_files():
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in {"eval", "exec", "compile"}):
                bad.append(f"{p}:{node.lineno}")
    assert bad == []


def test_no_cross_module_internal_imports():
    bad = []
    for p in py_files():
        relative = p.relative_to(SRC)
        if len(relative.parts) == 1:
            continue                 # a top-level composition module (report_stages.py) wires the modules
        owner = relative.parts[0]
        for m in re.finditer(r"src\.(\w+)\.(_\w+)", p.read_text(encoding="utf-8")):
            if m.group(1) in MODULES and m.group(1) != owner:
                bad.append(f"{relative} imports src.{m.group(1)}.{m.group(2)}")
    assert bad == []


def test_no_global_random_state():
    bad = [str(p) for p in py_files()
           if re.search(r"np\.random\.(rand|randn|randint|normal|uniform|choice|seed|shuffle)\(|(?<![\w.])random\.\w+\(",
                        p.read_text(encoding="utf-8"))]
    assert bad == []
