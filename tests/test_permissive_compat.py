from __future__ import annotations

import importlib
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
    assert pycountry.languages.get(alpha_2="en").name == "English"
    from podcast_creator.language import resolve_language_name

    assert resolve_language_name("pt-BR") == "Portuguese"
    assert resolve_language_name("eng") == "English"


def test_tqdm_compat_iterates_and_tracks_progress() -> None:
    progress = tqdm([1, 2, 3])
    assert list(progress) == [1, 2, 3]
    assert progress.n == 3


def test_transitive_consumers_import_with_compatibility_facades() -> None:
    for module in ("openai", "huggingface_hub", "proglog", "readability"):
        importlib.import_module(module)
