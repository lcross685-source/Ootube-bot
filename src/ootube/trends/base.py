"""Trend source interface.

Each source converts some third-party feed into ``TrendSignal`` objects. The
contract is narrow on purpose: ``fetch`` never raises, and returns an empty
list when the upstream service is unreachable. A scheduled run degrades to
fewer sources rather than failing.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Config
from ..models import TrendSignal

log = logging.getLogger(__name__)

registry: dict[str, type["TrendSource"]] = {}


class TrendSource:
    name = "base"
    #: News sources carry a real event date; chart-style sources measure
    #: current attention and are treated as always "now".
    dated = True

    def __init__(self, config: Config):
        self.config = config
        self.settings: dict[str, Any] = (config.sources or {}).get(self.name, {}) or {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "name", "base") != "base":
            registry[cls.name] = cls

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    def fetch(self) -> list[TrendSignal]:
        """Return signals, swallowing all upstream errors."""
        if not self.enabled:
            return []
        try:
            signals = list(self._fetch())
        except Exception as exc:  # noqa: BLE001 - a broken source must not kill the run
            log.warning("source %s failed: %s", self.name, exc)
            return []
        log.info("source %s produced %d signals", self.name, len(signals))
        return signals

    def _fetch(self) -> list[TrendSignal]:  # pragma: no cover - overridden
        raise NotImplementedError


def build_sources(
    config: Config, only: list[str] | None = None
) -> list[TrendSource]:
    """Instantiate every registered, enabled source."""
    sources: list[TrendSource] = []
    for name, cls in registry.items():
        if only and name not in only:
            continue
        src = cls(config)
        if src.enabled:
            sources.append(src)
    return sources
