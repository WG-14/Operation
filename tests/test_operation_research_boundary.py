from __future__ import annotations

from pathlib import Path

from bithumb_bot.cli.registry import command_registry


def test_operation_has_no_research_package_or_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "src" / "bithumb_bot"
    assert not (source / "research").exists()
    imports = [
        str(path)
        for path in source.rglob("*.py")
        if "bithumb_bot.research" in path.read_text(encoding="utf-8")
        or "from .research" in path.read_text(encoding="utf-8")
    ]
    assert not imports
    assert not any(name.startswith("research-") for name in command_registry())
