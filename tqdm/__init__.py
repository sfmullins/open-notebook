"""Minimal MIT progress API used by non-interactive Open Notebook dependencies."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class tqdm(Generic[T]):
    def __init__(self, iterable: Iterable[T] | None = None, total: int | None = None, *args: Any, **kwargs: Any) -> None:
        self.iterable = iterable
        self.total = total if total is not None else self._safe_len(iterable)
        self.n = int(kwargs.get("initial", 0) or 0)
        self.disable = bool(kwargs.get("disable", False))
        self.desc = kwargs.get("desc")

    @staticmethod
    def _safe_len(iterable: Iterable[Any] | None) -> int | None:
        if iterable is None:
            return None
        try:
            return len(iterable)  # type: ignore[arg-type]
        except (TypeError, AttributeError):
            return None

    def __iter__(self) -> Iterator[T]:
        if self.iterable is None:
            return iter(())
        for item in self.iterable:
            yield item
            self.update(1)

    def __enter__(self) -> "tqdm[T]":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def update(self, n: int | float = 1) -> bool | None:
        self.n += int(n)
        return None

    def close(self) -> None:
        return None

    def refresh(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def clear(self, *args: Any, **kwargs: Any) -> None:
        return None

    def reset(self, total: int | None = None) -> None:
        self.n = 0
        if total is not None:
            self.total = total

    def set_description(self, desc: str | None = None, refresh: bool = True) -> None:
        self.desc = desc

    set_description_str = set_description

    def set_postfix(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set_postfix_str(self, *args: Any, **kwargs: Any) -> None:
        return None

    @classmethod
    def write(cls, s: str, file: Any = None, end: str = "\n", nolock: bool = False) -> None:
        print(s, file=file, end=end)

    @classmethod
    def get_lock(cls) -> None:
        return None

    @classmethod
    def set_lock(cls, lock: Any) -> None:
        return None


def trange(*args: int, **kwargs: Any) -> tqdm[int]:
    return tqdm(range(*args), **kwargs)


__all__ = ["tqdm", "trange"]
__version__ = "compat"
