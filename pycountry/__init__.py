"""MIT compatibility subset for podcast-creator language resolution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from iso639 import Lang


@dataclass(frozen=True)
class _Language:
    name: str


class _Languages:
    def get(self, **kwargs: Any) -> _Language | None:
        code = kwargs.get("alpha_2") or kwargs.get("alpha_3") or kwargs.get("name")
        if not code:
            return None
        try:
            language = Lang(str(code))
        except Exception:
            return None
        return _Language(name=language.name)


languages = _Languages()
__all__ = ["languages"]
