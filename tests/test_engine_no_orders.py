"""Structural safety guard: nothing under trading_bot/engine/ (or the
run_technical entrypoint) may reach an order-mutating call - not by
attribute name, not by importing the modules that wrap one. Spec §34/§35
("pre-signal never places orders", "strategy never places orders") and §50
("paper mode must make live order placement technically impossible").

When M2 adds an execution subpackage, this test is deliberately what has
to change - loosen it ONLY for that subpackage, by explicit allow-list, so
the loosening itself shows up in review."""
import ast
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent / "trading_bot" / "engine"
ENTRYPOINT = Path(__file__).resolve().parent.parent / "trading_bot" / "run_technical.py"

FORBIDDEN_ATTRS = {"place_order", "modify_order", "cancel_order", "place_split_order"}
# Modules whose only reason to exist is to place orders.
FORBIDDEN_MODULES = {"trading_bot.strategy", "trading_bot.debit_strategy", "trading_bot.equity_strategy"}


def _scan(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
            hits.append(f"{path.name}:{node.lineno} attribute {node.attr}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_ATTRS:
            hits.append(f"{path.name}:{node.lineno} name {node.id}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_MODULES:
                    hits.append(f"{path.name}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module in FORBIDDEN_MODULES:
            hits.append(f"{path.name}:{node.lineno} from {node.module} import ...")
    return hits


def test_engine_package_never_touches_order_placement():
    files = [p for p in ENGINE_DIR.rglob("*.py") if "__pycache__" not in p.parts]
    assert files, "engine package should have modules"
    hits = [h for f in files for h in _scan(f)]
    assert not hits, "order-placement reachable from engine/:\n" + "\n".join(hits)


def test_entrypoint_never_touches_order_placement():
    assert not _scan(ENTRYPOINT)
