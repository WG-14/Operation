from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


Printer = Callable[[str], None]


@dataclass(slots=True)
class AppContext:
    """Runtime dependencies for CLI command execution.

    Most fields are optional so parser construction and read-only smoke tests
    can build a context without opening the DB or constructing a broker.
    """

    argv: Sequence[str] | None = None
    settings: Any | None = None
    path_manager: Any | None = None
    db_factory: Callable[..., Any] | None = None
    broker_factory: Callable[..., Any] | None = None
    notifier: Callable[[str], Any] | None = None
    clock: Callable[[], Any] | None = None
    printer: Printer = print
    env_summary: Any | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def build_default_context(argv: Sequence[str] | None = None) -> AppContext:
    """Build the default CLI context without creating broker or DB handles."""

    from operation.bootstrap import get_last_explicit_env_load_summary
    from operation.config import PATH_MANAGER, settings

    return AppContext(
        argv=list(sys.argv[1:] if argv is None else argv),
        settings=settings,
        path_manager=PATH_MANAGER,
        env_summary=get_last_explicit_env_load_summary(),
    )
