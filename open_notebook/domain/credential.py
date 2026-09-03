"""Credential domain model for storing individual provider credentials."""

from typing import Any, ClassVar, Dict, List, Optional

from loguru import logger
from pydantic import SecretStr, model_validator

from open_notebook.database.repository import repo_list
from open_notebook.domain.base import ObjectModel
from open_notebook.utils.encryption import decrypt_value, encrypt_value


class Credential(ObjectModel):
    """Individual credential record for an AI provider."""

    table_name: ClassVar[str] = "credential"
    nullable_fields: ClassVar[set[str]] = {
        "api_key",
        "base_url",
        "endpoint",
        "api_version",
        "endpoint_llm",
        "endpoint_embedding",
        "endpoint_stt",
        "endpoint_tts",
        "project",
        "location",
        "credentials_path",
    }
    CONFIG_EXTRAS: ClassVar[set[str]] = {"num_ctx"}

    name: str
    provider: str
    modalities: List[str] = []
    api_key: Optional[SecretStr] = None
    decryption_error: Optional[str] = None
    base_url: Optional[str] = None
    endpoint: Optional[str] = None
    api_version: Optional[str] = None
    endpoint_llm: Optional[str] = None
    endpoint_embedding: Optional[str] = None
    endpoint_stt: Optional[str] = None
    endpoint_tts: Optional[str] = None
    project: Optional[str] = None
    location: Optional[str] = None
    credentials_path: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    num_ctx: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _mirror_config_to_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("config"), dict):
            config = data["config"]
            data = dict(data)
            for key in cls.CONFIG_EXTRAS:
                if data.get(key) is None and config.get(key) is not None:
                    data[key] = config[key]
        return data

    def to_esperanto_config(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {}
        if self.api_key:
            config["api_key"] = self.api_key.get_secret_value()
        if self.base_url:
            config["base_url"] = self.base_url
            if self.provider and self.provider.lower() == "azure" and not self.endpoint:
                config["endpoint"] = self.base_url
        if self.endpoint:
            config["endpoint"] = self.endpoint
        if self.api_version:
            config["api_version"] = self.api_version
        if self.endpoint_llm:
            config["endpoint_llm"] = self.endpoint_llm
        if self.endpoint_embedding:
            config["endpoint_embedding"] = self.endpoint_embedding
        if self.endpoint_stt:
            config["endpoint_stt"] = self.endpoint_stt
        if self.endpoint_tts:
            config["endpoint_tts"] = self.endpoint_tts
        is_vertex = bool(self.provider) and self.provider.lower() == "vertex"
        if self.project:
            config["vertex_project" if is_vertex else "project"] = self.project
        if self.location:
            config["vertex_location" if is_vertex else "location"] = self.location
        if self.credentials_path:
            config["credentials_path"] = self.credentials_path
        if self.num_ctx is not None:
            config["num_ctx"] = self.num_ctx
        return config

    @classmethod
    async def get_by_provider(cls, provider: str) -> List["Credential"]:
        results = await repo_list(
            "credential",
            filters={"provider": provider},
            case_insensitive_fields={"provider"},
            order_by="created",
        )
        credentials = []
        for row in results:
            try:
                credentials.append(cls._from_db_row(row))
            except Exception as e:
                logger.warning(f"Skipping invalid credential: {e}")
        return credentials

    @classmethod
    async def get(cls, id: str) -> "Credential":
        instance = await super().get(id)
        if instance.api_key:
            raw = (
                instance.api_key.get_secret_value()
                if isinstance(instance.api_key, SecretStr)
                else instance.api_key
            )
            decrypted = decrypt_value(raw)
            object.__setattr__(instance, "api_key", SecretStr(decrypted))
        return instance

    @classmethod
    async def get_all(cls, order_by=None) -> List["Credential"]:
        field = None
        descending = False
        if order_by:
            validated = cls._validate_order_by(order_by)
            first = validated.split(",", 1)[0].split()
            field = first[0]
            descending = len(first) == 2 and first[1] == "desc"
        results = await repo_list(cls.table_name, order_by=field, descending=descending)
        credentials = []
        for row in results:
            try:
                credentials.append(cls._from_db_row(row))
            except Exception as e:
                logger.warning(
                    f"Failed to decrypt credential {row.get('id', 'unknown')}: {e}"
                )
                try:
                    error_cred = cls(
                        name=row.get("name", "Unknown"),
                        provider=row.get("provider", "unknown"),
                        modalities=row.get("modalities", []),
                        decryption_error="Failed to decrypt API key. The encryption key may have changed.",
                    )
                    if row.get("id"):
                        object.__setattr__(error_cred, "id", str(row["id"]))
                    if row.get("created"):
                        object.__setattr__(error_cred, "created", row["created"])
                    if row.get("updated"):
                        object.__setattr__(error_cred, "updated", row["updated"])
                    if row.get("api_key"):
                        object.__setattr__(error_cred, "api_key", SecretStr("UNDECRYPTABLE"))
                    credentials.append(error_cred)
                except Exception as inner_e:
                    logger.error(
                        f"Failed to create error credential for {row.get('id', 'unknown')}: {inner_e}"
                    )
        return credentials

    async def get_linked_models(self) -> list:
        if not self.id:
            return []
        from open_notebook.ai.models import Model

        results = await repo_list("model", filters={"credential": self.id})
        return [Model(**row) for row in results]

    def _prepare_save_data(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        for key, value in self.model_dump().items():
            if key in ("decryption_error", "config"):
                continue
            if key == "api_key":
                if self.api_key:
                    data["api_key"] = encrypt_value(self.api_key.get_secret_value())
                else:
                    data["api_key"] = None
            elif value is not None or key in self.__class__.nullable_fields:
                data[key] = value

        config: Dict[str, Any] = dict(self.config or {})
        for key in self.__class__.CONFIG_EXTRAS:
            data.pop(key, None)
            value = getattr(self, key, None)
            if value is not None:
                config[key] = value
            else:
                config.pop(key, None)
        data["config"] = config or None
        return data

    async def save(self) -> None:
        original_api_key = self.api_key
        await super().save()
        if original_api_key:
            object.__setattr__(self, "api_key", original_api_key)
        elif self.api_key and isinstance(self.api_key, str):
            decrypted = decrypt_value(self.api_key)
            object.__setattr__(self, "api_key", SecretStr(decrypted))

    @classmethod
    def _from_db_row(cls, row: dict) -> "Credential":
        row = dict(row)
        api_key_val = row.get("api_key")
        if api_key_val and isinstance(api_key_val, str):
            decrypted = decrypt_value(api_key_val)
            row["api_key"] = SecretStr(decrypted)
        elif api_key_val is None:
            row["api_key"] = None
        return cls(**row)
