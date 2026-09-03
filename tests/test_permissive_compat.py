from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
from pathlib import Path

import certifi
import chardet
import pycountry
from tqdm import tqdm


def test_certifi_uses_real_system_bundle() -> None:
    assert Path(certifi.where()).is_file()


def test_chardet_compat_detects_text() -> None:
    result = chardet.detect("Vält test".encode())
    assert result["encoding"]
    assert 0 <= result["confidence"] <= 1


def test_pycountry_compat_and_podcast_creator_language_resolution() -> None:
    english = pycountry.languages.get(alpha_2="en")
    assert english is not None
    assert english.name == "English"
    assert english.alpha_2 == "en"
    assert any(language.alpha_2 == "en" for language in pycountry.languages)

    # podcast_creator.__init__ eagerly imports its media pipeline, which asks
    # imageio-ffmpeg for an FFmpeg executable at import time. FFmpeg is an
    # intentionally external optional runtime dependency in Vält. Load the
    # installed, self-contained upstream language module directly so this test
    # exercises its real pycountry consumer without crossing that media boundary.
    distribution = importlib.metadata.distribution("podcast-creator")
    language_path = distribution.locate_file("podcast_creator/language.py")
    spec = importlib.util.spec_from_file_location(
        "_podcast_creator_language_compat", language_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    resolve_language_name = getattr(module, "resolve_language_name")

    assert resolve_language_name("pt-BR") == "Portuguese"
    assert resolve_language_name("eng") == "English"


def test_tqdm_compat_iterates_and_tracks_progress() -> None:
    progress = tqdm([1, 2, 3])
    assert list(progress) == [1, 2, 3]
    assert progress.n == 3


def test_transitive_consumers_import_with_compatibility_facades() -> None:
    for module in ("openai", "huggingface_hub", "proglog", "readability"):
        importlib.import_module(module)
