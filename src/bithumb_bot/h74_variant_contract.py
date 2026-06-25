from __future__ import annotations

from collections.abc import Mapping

from .h74_observation import H74ObservationAuthorityError


ALLOWED_VARIANT_OVERRIDE_KEYS = frozenset(
    {
        "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST",
        "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST",
    }
)


def allowed_variant_override_keys() -> tuple[str, ...]:
    return tuple(sorted(ALLOWED_VARIANT_OVERRIDE_KEYS))


def validate_h74_variant_overrides(variant_overrides: Mapping[str, object]) -> dict[str, object]:
    overrides = {str(key): value for key, value in dict(variant_overrides or {}).items()}
    forbidden = sorted(set(overrides) - ALLOWED_VARIANT_OVERRIDE_KEYS)
    if forbidden:
        raise H74ObservationAuthorityError("forbidden_variant_override:" + ",".join(forbidden))
    if not overrides:
        raise H74ObservationAuthorityError("h74_source_variant_authority_variant_overrides_missing")
    return overrides
