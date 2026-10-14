"""Shared switch for automatic alternate-account detection."""

import config


def alt_detection_enabled() -> bool:
    """Require explicit opt-in, including for older configurations."""
    value = getattr(config, "ALT_DETECTION_ENABLED", False)
    return str(value).strip().lower() in ("1", "true", "yes")
