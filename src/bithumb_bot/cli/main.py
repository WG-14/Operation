from __future__ import annotations

from .context import AppContext, build_default_context
from .dispatch import dispatch
from .parser import build_parser
from .registry import command_registry


def main(argv: list[str] | None = None, context: AppContext | None = None) -> int:
    registry = command_registry()
    parser = build_parser(registry)
    args, _unknown = parser.parse_known_args(argv)
    app_context = context or build_default_context(argv)
    return dispatch(args, app_context, registry)


if __name__ == "__main__":
    raise SystemExit(main())
