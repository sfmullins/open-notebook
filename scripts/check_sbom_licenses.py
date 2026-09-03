#!/usr/bin/env python3
"""Enforce Vält licence policy against a Syft JSON SBOM.

The final image contains two materially different licensing domains:

* application dependencies (Python and npm), which must be permissively licensed;
* Debian/base-runtime packages, which are operating-system infrastructure and may
  include GPL/LGPL components. Those are still rejected for network/source-
  available, non-commercial, source-restricting, or otherwise prohibited terms.

Syft also reports Rust crates and PE/ELF binaries embedded inside packaged tools.
Those entries frequently omit licence metadata even when the owning Python/Debian
package declares it, so policy is enforced at the owning package ecosystems rather
than treating embedded-component NOASSERTION entries as independent distributions.

Some Python wheels publish incomplete or opaque Core Metadata. Exact-version
reviewed overrides below supply a canonical expression only for those scanner
metadata gaps. An override never masks a clear licence expression emitted by Syft,
and upgrades fail closed until the new version is reviewed.
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
    "public-domain",
    "unlicense",
    "zlib",
}

# Explicitly prohibited regardless of package ecosystem. A bare Debian
# "noderivs" label is intentionally not included: Debian uses it for verbatim
# copies of licence texts themselves (for example COPYING.GPLv2 in xz-utils),
# not as a restriction on the software. Explicit no-derivatives content licences
# remain prohibited below.
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
}

# These are not permissive and therefore normally fail for Python/npm.
COPYLEFT_FRAGMENTS = {
    "gpl",
    "lgpl",
    "mpl",
    "epl",
    "cddl",
}

# Exact-version, independently reviewed metadata overrides. These do not waive
# policy: the canonical expression is still evaluated by app_licence_is_permissive.
# The source is printed in CI whenever an override is consumed.
APP_LICENSE_OVERRIDES: dict[tuple[str, str, str], tuple[str, str]] = {
    ("python", "aiosqlite", "0.22.1"): (
        "MIT",
        "https://pypi.org/project/aiosqlite/0.22.1/",
    ),
    ("python", "annotated-types", "0.7.0"): (
        "MIT",
        "https://pypi.org/project/annotated-types/0.7.0/",
    ),
    ("python", "content-core", "2.0.4"): (
        "MIT",
        "https://pypi.org/project/content-core/2.0.4/",
    ),
    # Docutils' installed Python module is public-domain/BSD-2-Clause. The GPL
    # exception listed by upstream is tools/editors/emacs/rst.el; it is not in
    # the installed wheel (the SBOM records only the site-packages module).
    ("python", "docutils", "0.22.4"): (
        "public-domain AND BSD-2-Clause",
        "https://docutils.sourceforge.io/COPYING.html",
    ),
    ("python", "exceptiongroup", "1.3.1"): (
        "MIT",
        "https://pypi.org/project/exceptiongroup/1.3.1/",
    ),
    ("python", "iso639-lang", "2.6.3"): (
        "MIT",
        "https://pypi.org/project/iso639-lang/2.6.3/",
    ),
    ("python", "jinja2", "3.1.6"): (
        "BSD-3-Clause",
        "https://pypi.org/project/Jinja2/3.1.6/",
    ),
    ("python", "jiter", "0.12.0"): (
        "MIT",
        "https://pypi.org/project/jiter/0.12.0/",
    ),
    ("python", "loguru", "0.7.3"): (
        "MIT",
        "https://pypi.org/project/loguru/0.7.3/",
    ),
    ("python", "markdown-it-py", "4.0.0"): (
        "MIT",
        "https://pypi.org/project/markdown-it-py/4.0.0/",
    ),
    # pytubefix uses nodejs_wheel.executable only to locate bin/node. The Docker
    # build strips the wheel's unused npm/npx payload, which is independently
    # scanned before removal and is not part of the final runtime image.
    ("python", "nodejs-wheel-binaries", "24.13.0"): (
        "MIT",
        "https://pypi.org/project/nodejs-wheel-binaries/24.13.0/",
    ),
    ("python", "packaging", "25.0"): (
        "Apache-2.0 OR BSD-2-Clause",
        "https://pypi.org/project/packaging/25.0/",
    ),
    ("python", "pandas", "3.0.0"): (
        "BSD-3-Clause",
        "https://pypi.org/project/pandas/3.0.0/",
    ),
    ("python", "pdfplumber", "0.11.10"): (
        "MIT",
        "https://pypi.org/project/pdfplumber/0.11.10/",
    ),
    ("python", "python-dateutil", "2.9.0.post0"): (
        "Apache-2.0 OR BSD-3-Clause",
        "https://pypi.org/project/python-dateutil/2.9.0.post0/",
    ),
    ("python", "socksio", "1.0.0"): (
        "MIT",
        "https://pypi.org/project/socksio/1.0.0/",
    ),
    ("python", "tokenizers", "0.22.2"): (
        "Apache-2.0",
        "https://pypi.org/project/tokenizers/0.22.2/",
    ),
    ("python", "uuid-utils", "0.14.0"): (
        "BSD-3-Clause",
        "https://pypi.org/project/uuid-utils/0.14.0/",
    ),
}


def artifact_paths(artifact: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for location in artifact.get("locations") or []:
        if isinstance(location, dict) and location.get("path"):
            paths.append(str(location["path"]))
    return paths


def is_os_owned_python(artifact: dict[str, Any]) -> bool:
    return any(
        path.startswith("/usr/lib/python3/dist-packages/")
        or path.startswith("/usr/lib/python3.") and "/dist-packages/" in path
        for path in artifact_paths(artifact)
    )


def is_owned_next_bundle(artifact: dict[str, Any]) -> bool:
    return any(
        "/node_modules/next/dist/compiled/" in path
        for path in artifact_paths(artifact)
    )


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


def is_opaque_licence_value(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        normalized in {"noassertion", "unknown", "dual license"}
        or normalized.startswith("sha256:")
    )


def reviewed_override(
    kind: str, name: str, version: str
) -> tuple[str, str] | None:
    return APP_LICENSE_OVERRIDES.get((kind, name.lower(), version))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Syft JSON SBOM licence policy")
    parser.add_argument("sbom", type=Path)
    args = parser.parse_args()

    data = json.loads(args.sbom.read_text(encoding="utf-8"))
    failures: list[str] = []
    used_overrides: list[str] = []
    seen: defaultdict[str, int] = defaultdict(int)

    for artifact in data.get("artifacts", []):
        kind = str(artifact.get("type") or "unknown")
        name = str(artifact.get("name") or "<unnamed>")
        version = str(artifact.get("version") or "<unknown>")
        values = licence_values(artifact)
        seen[kind] += 1

        if kind == "python" and is_os_owned_python(artifact):
            for value in values:
                lower = value.lower()
                if any(fragment in lower for fragment in PROHIBITED_FRAGMENTS):
                    failures.append(
                        f"{kind}:{name}@{version}: prohibited OS-owned Python licence term {value!r}"
                    )
            continue

        if kind == "npm" and is_owned_next_bundle(artifact):
            # Next.js is itself policy-checked; its dist/compiled modules are
            # vendored implementation details with incomplete nested metadata.
            continue

        if kind in APP_TYPES:
            override = reviewed_override(kind, name, version)
            metadata_is_opaque = not values or all(
                is_opaque_licence_value(value) for value in values
            )

            if metadata_is_opaque:
                if override is None:
                    failures.append(
                        f"{kind}:{name}@{version}: no usable licence metadata ({values or ['NONE']})"
                    )
                    continue
                expression, source = override
                if not app_licence_is_permissive(expression):
                    failures.append(
                        f"{kind}:{name}@{version}: reviewed override is not permissive: {expression!r}"
                    )
                    continue
                used_overrides.append(
                    f"{kind}:{name}@{version}: {expression}; source={source}; detected={values or ['NONE']}"
                )
                continue

            bad = [value for value in values if not app_licence_is_permissive(value)]
            if bad:
                # A reviewed override never hides explicit scanner metadata. If
                # upstream starts declaring a copyleft/restricted licence, fail.
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

    if used_overrides:
        print("Reviewed application licence metadata overrides used:")
        for item in sorted(set(used_overrides)):
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
