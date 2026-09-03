#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# pyproject: exclude GPL asciidoc only from content-core and prevent binary
# imageio-ffmpeg wheels (which may embed an FFmpeg executable) from installing.
p = ROOT / "pyproject.toml"
text = p.read_text(encoding="utf-8")
old = '''[tool.uv]
# Pillow < 12.2.0 has open security advisories (PSD OOB write, FITS
# decompression bomb, PDF trailer DoS). The only thing holding it back is
# moviepy's `pillow<12` cap, pulled in via podcast-creator 0.12.0 — and
# moviepy only touches PIL in its video modules, which the audio-only podcast
# pipeline never imports. Drop this override once podcast-creator ships a
# release without moviepy (already removed on its main branch).
override-dependencies = ["pillow>=12.2.0"]
'''
new = '''[tool.uv]
# Vält shipped userland is permissive-only. content-core 2.0.4 declares
# asciidoc 10.2.1 (GPL-2.0-or-later), but does not import it in the extraction
# paths Open Notebook uses. Exclude only that transitive edge; CI smoke-tests
# content-core extraction without it so this cannot silently become required.
exclude-dependencies = [
    { package = { name = "content-core" }, dependencies = ["asciidoc"] },
]

# imageio-ffmpeg is a permissive Python wrapper, but its binary wheels can
# contain an FFmpeg executable. Force source installation so Vält never
# redistributes that binary. Media/podcast features use an operator-supplied
# ffmpeg/ffprobe installation outside the Vält userland boundary.
no-binary-package = ["imageio-ffmpeg"]

# Pillow < 12.2.0 has open security advisories (PSD OOB write, FITS
# decompression bomb, PDF trailer DoS). The only thing holding it back is
# moviepy's `pillow<12` cap, pulled in via podcast-creator 0.12.0 — and
# moviepy only touches PIL in its video modules, which the audio-only podcast
# pipeline never imports. Drop this override once podcast-creator ships a
# release without moviepy (already removed on its main branch).
override-dependencies = ["pillow>=12.2.0"]
'''
if text.count(old) != 1:
    raise RuntimeError("pyproject [tool.uv] block drifted")
p.write_text(text.replace(old, new, 1), encoding="utf-8")

# Dockerfile: pin all language/tool images, eliminate redistributed FFmpeg,
# and eliminate the mutable NodeSource bootstrap by copying the already-pinned
# Node runtime executable from the frontend stage.
p = ROOT / "Dockerfile"
text = p.read_text(encoding="utf-8")
text = text.replace("FROM node:22-slim AS frontend-builder", "FROM node:22.23.2-slim AS frontend-builder")
text = text.replace("FROM python:3.12-slim-trixie AS backend-builder", "FROM python:3.12.14-slim-trixie AS backend-builder")
text = text.replace("FROM python:3.12-slim-trixie AS runtime", "FROM python:3.12.14-slim-trixie AS runtime")
text = text.replace("ghcr.io/astral-sh/uv:latest", "ghcr.io/astral-sh/uv:0.12.9")
old_runtime = '''RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \\
    ffmpeg \\
    supervisor \\
    curl \\
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \\
    && apt-get install -y --no-install-recommends nodejs \\
    && rm -rf /var/lib/apt/lists/*
'''
new_runtime = '''# FFmpeg is deliberately NOT installed in this image. Vält does not redistribute
# the FFmpeg executable; operators who enable media/podcast features provide it
# externally. Node is copied from the pinned frontend stage instead of using a
# mutable remote installation script.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \\
    supervisor \\
    && rm -rf /var/lib/apt/lists/*
COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node
'''
if text.count(old_runtime) != 1:
    raise RuntimeError("Docker runtime install block drifted")
text = text.replace(old_runtime, new_runtime, 1)
p.write_text(text, encoding="utf-8")

# Clean stale runtime comments now that SurrealDB is migration-only.
p = ROOT / "commands/source_commands.py"
text = p.read_text(encoding="utf-8")
text = text.replace(
    "# Handle deep queues (workaround for SurrealDB v2 transaction conflicts)",
    "# Handle deep queues and transient database contention",
)
text = text.replace("# Avoid log noise during transaction conflicts", "# Avoid log noise during transient contention")
p.write_text(text, encoding="utf-8")

# Self-delete helper/workflow in the generated commit.
for rel in (
    "scripts/pr2_license_blockers.py",
    ".github/workflows/pr2-license-blockers.yml",
):
    target = ROOT / rel
    if target.exists():
        target.unlink()
