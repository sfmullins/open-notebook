#!/usr/bin/env python3
"""Enforce Vält licence policy against a Syft JSON SBOM.

The final image contains two materially different licensing domains:

* application dependencies (Python and npm), which must be permissively licensed
  unless a package has a narrow, reviewed exception recorded below; and
* Debian/base-runtime packages, which are operating-system infrastructure and may
  include GPL/LGPL components. Those are still rejected for network/source-
  available, non-commercial, source-restricting, or otherwise prohibited terms.

Syft also reports Rust crates and PE/ELF binaries embedded inside packaged tools.
Those entries frequently omit licence metadata even when the owning Python/Debian
package declares it, so policy is enforced at the owning package ecosystems rather
than treating embedded-component NOASSERTION entries as independent distributions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

APP_TYPES = {"python", "npm"}
OS_TYPES = {"deb"}

# Tokens/families acceptable for the shipped application dependency layer.
# Matching is intentionally conservative: every detected expression must contain
# only these licence families/operators after normalization.
PERMISSIVE_TOKENS = {
    "0bsd",
    "apache",
    "apache-2.0",
    "artistic-2.0",
    "bsd",
    "bsd-2-clause",
    "bsd-3-clause",
    "blueoak-1.0.0",
    "cc0-1.0",
    "cnri-python",
    "isc",
    "mit",
    "mit-cmu",
    "psf-2.0",
    "psfl",
    "unlicense",
    "zlib",
}

# Explicitly prohibited regardless of package ecosystem.
PROHIBITED_FRAGMENTS = {
    "agpl",
    "sspl",
    "server side public license",
    "business source license",
    "bsl-1.1",
    "elastic license",
    "commons clause",
    "non-commercial",
    "noncommercial",
    "cc-by-nc",
    "cc-by-nd",
    "noderivs",
}

# These are not permissive and therefore normally fail for Python/npm.
COPYLEFT_FRAGMENTS = {
    "gpl",
    "lgpl",
    "mpl",
    "epl",
    "cddl",
}

# Reviewed metadata exceptions must be keyed to the exact package name and may
# only be used where the actual package licence has separately been confirmed.
# Keep this list as small as possible; CI prints every exception it consumes.
APP_METADATA_EXCEPTIONS: dict[tuple[str, str], str] = {}


def licence_values(artifact: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in artifact.get("licenses") or []:
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            value = item.get("spdxExpression") or item.get("value") or ""
        else:
            value = ""
        value = str(value).strip()
        if value:
            values.append(value)
    return values


def normalized_words(value: str) -> set[str]:
    text = value.lower()
    # Human-readable variants seen in Python metadata.
    text = text.replace("apache license, version 2.0", "apache-2.0")
    text = text.replace("apache license 2.0", "apache-2.0")
    text = text.replace("apache 2.0", "apache-2.0")
    text = text.replace("mit license", "mit")
    text = text.replace("isc license", "isc")
    text = text.replace("modified bsd license", "bsd-3-clause")
    text = text.replace("dual license", "")
    text = text.replace("dependency licenses", "")
    # Operators, punctuation and URLs are not licence identifiers.
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[(),]", " ", text)
    words = {
        word.strip().lower()
        for word in re.split(r"\s+", text)
        if word.strip() and word.lower() not in {"and", "or", "license", "version"}
    }
    return words


def app_licence_is_permissive(value: str) -> bool:
    lower = value.lower()
    if any(fragment in lower for fragment in PROHIBITED_FRAGMENTS):
        return False
    if any(fragment in lower for fragment in COPYLEFT_FRAGMENTS):
        return False
    words = normalized_words(value)
    return bool(words) and words <= PERMISSIVE_TOKENS


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Syft JSON SBOM licence policy")
    parser.add_argument("sbom", type=Path)
    args = parser.parse_args()

    data = json.loads(args.sbom.read_text(encoding="utf-8"))
    failures: list[str] = []
    used_exceptions: list[str] = []
    seen: defaultdict[str, int] = defaultdict(int)

    for artifact in data.get("artifacts", []):
        kind = str(artifact.get("type") or "unknown")
        name = str(artifact.get("name") or "<unnamed>")
        version = str(artifact.get("version") or "<unknown>")
        values = licence_values(artifact)
        seen[kind] += 1

        if kind in APP_TYPES:
            key = (kind, name)
            if not values or any(value.upper() in {"NOASSERTION", "UNKNOWN"} for value in values):
                reason = APP_METADATA_EXCEPTIONS.get(key)
                if reason:
                    used_exceptions.append(f"{kind}:{name}@{version}: {reason}")
                    continue
                failures.append(
                    f"{kind}:{name}@{version}: no usable licence metadata ({values or ['NONE']})"
                )
                continue
            bad = [value for value in values if not app_licence_is_permissive(value)]
            if bad:
                reason = APP_METADATA_EXCEPTIONS.get(key)
                if reason:
                    used_exceptions.append(f"{kind}:{name}@{version}: {reason}; detected={bad}")
                else:
                    failures.append(
                        f"{kind}:{name}@{version}: non-permissive/unreviewed licence {bad}"
                    )
            continue

        if kind in OS_TYPES:
            for value in values:
                lower = value.lower()
                if any(fragment in lower for fragment in PROHIBITED_FRAGMENTS):
                    failures.append(
                        f"{kind}:{name}@{version}: prohibited base-runtime licence term {value!r}"
                    )

    print("SBOM package counts:")
    for kind in sorted(seen):
        print(f"  {kind}: {seen[kind]}")

    if used_exceptions:
        print("Reviewed application metadata exceptions used:")
        for item in sorted(set(used_exceptions)):
            print(f"  {item}")

    if failures:
        print("Licence policy failures:", file=sys.stderr)
        for failure in sorted(set(failures)):
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Full-image licence policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
