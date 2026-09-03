"""MIT compatibility subset for the pycountry language API used by Open Notebook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from iso639 import Lang, iter_langs


@dataclass(frozen=True)
class _Language:
    name: str
    alpha_2: str | None = None
    alpha_3: str | None = None


class _Languages:
    def __iter__(self) -> Iterator[_Language]:
        """Yield ISO 639-1 languages in the shape used by the languages API."""
        for language in iter_langs():
            alpha_2 = language.pt1
            if not alpha_2:
                continue
            yield _Language(
                name=language.name,
                alpha_2=alpha_2,
                alpha_3=language.pt3 or language.pt2t or language.pt2b or None,
            )

    def get(self, **kwargs: Any) -> _Language | None:
        code = kwargs.get("alpha_2") or kwargs.get("alpha_3") or kwargs.get("name")
        if not code:
            return None
        try:
            language = Lang(str(code))
        except (KeyError, ValueError):
            return None
        return _Language(
            name=language.name,
            alpha_2=language.pt1 or None,
            alpha_3=language.pt3 or language.pt2t or language.pt2b or None,
        )


languages = _Languages()
__all__ = ["languages"]
