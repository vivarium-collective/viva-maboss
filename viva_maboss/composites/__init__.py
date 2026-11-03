"""Composite generators for viva-maboss (imported for their @composite_generator side effects)."""
from . import toggle  # noqa: F401
from .toggle import TOGGLE_NETWORK, toggle_switch

__all__ = ["TOGGLE_NETWORK", "toggle_switch"]
