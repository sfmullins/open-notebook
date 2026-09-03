#!/usr/bin/env python3
"""Apply the two deterministic type-narrowing fixes identified by mypy."""

from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        # The fix may already have been applied by a prior successful run.
        if new in text:
            return
        raise RuntimeError(f"{label}: expected source pattern was not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    provider = Path("open_notebook/domain/provider_config.py")
    replace_once(
        provider,
        """            for provider, provider_creds in creds_data.items():
                if not isinstance(provider_creds, list):
                    continue
                credentials[provider] = []
                for raw in provider_creds:
                    try:
                        cred_data = dict(raw)
""",
        """            for provider, provider_creds in creds_data.items():
                if not isinstance(provider_creds, list):
                    continue
                provider_name = str(provider)
                credentials[provider_name] = []
                for raw in provider_creds:
                    try:
                        cred_data = dict(raw)
""",
        "provider credential map key",
    )
    replace_once(
        provider,
        """                        credentials[provider].append(
                            ProviderCredential(
                                id=cred_data.get("id", ""),
                                name=cred_data.get("name", "Default"),
                                provider=cred_data.get("provider", provider),
""",
        """                        provider_value = cred_data.get("provider")
                        if not isinstance(provider_value, str) or not provider_value:
                            provider_value = provider_name
                        credentials[provider_name].append(
                            ProviderCredential(
                                id=cred_data.get("id", ""),
                                name=cred_data.get("name", "Default"),
                                provider=provider_value,
""",
        "provider credential value",
    )

    discovery = Path("open_notebook/ai/model_discovery.py")
    replace_once(
        discovery,
        """    result = []
    grouped: Dict[str, int] = {}
    for row in rows:
        model_type = row.get("type")
        if model_type:
            grouped[model_type] = grouped.get(model_type, 0) + 1
    result = [{"type": key, "count": value} for key, value in grouped.items()]

    counts = {
        "language": 0,
        "embedding": 0,
        "speech_to_text": 0,
        "text_to_speech": 0,
    }

    for row in result:
        model_type = row.get("type")
        count = row.get("count", 0)
        if model_type in counts:
            counts[model_type] = count

    return counts
""",
        """    grouped: Dict[str, int] = {}
    for row in rows:
        model_type = row.get("type")
        if isinstance(model_type, str) and model_type:
            grouped[model_type] = grouped.get(model_type, 0) + 1

    counts = {
        "language": 0,
        "embedding": 0,
        "speech_to_text": 0,
        "text_to_speech": 0,
    }

    for model_type, count in grouped.items():
        if model_type in counts:
            counts[model_type] = count

    return counts
""",
        "model type counts",
    )


if __name__ == "__main__":
    main()
