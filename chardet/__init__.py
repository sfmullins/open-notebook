"""MIT-compatible chardet subset backed by charset-normalizer."""
from __future__ import annotations

from typing import Any

from charset_normalizer import from_bytes


def detect(data: bytes | bytearray) -> dict[str, Any]:
    match = from_bytes(bytes(data)).best()
    if match is None:
        return {"encoding": None, "confidence": 0.0, "language": ""}
    chaos = float(getattr(match, "percent_chaos", 100.0)) / 100.0
    return {
        "encoding": match.encoding,
        "confidence": max(0.0, min(1.0, 1.0 - chaos)),
        "language": str(getattr(match, "language", "") or ""),
    }


class UniversalDetector:
    def __init__(self, *_: Any, **__: Any) -> None:
        self.reset()

    def reset(self) -> None:
        self._chunks: list[bytes] = []
        self.done = False
        self.result: dict[str, Any] = {"encoding": None, "confidence": 0.0, "language": ""}

    def feed(self, byte_str: bytes | bytearray) -> None:
        if not self.done:
            self._chunks.append(bytes(byte_str))

    def close(self) -> dict[str, Any]:
        self.result = detect(b"".join(self._chunks))
        self.done = True
        return self.result


__all__ = ["UniversalDetector", "detect"]
