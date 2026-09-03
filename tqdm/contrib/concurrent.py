from __future__ import annotations

from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

def thread_map(fn: Callable[[T], R], *iterables: Iterable[T], **kwargs: Any) -> list[R]:
    return list(map(fn, *iterables))

def process_map(fn: Callable[[T], R], *iterables: Iterable[T], **kwargs: Any) -> list[R]:
    return list(map(fn, *iterables))

__all__ = ["thread_map", "process_map"]
