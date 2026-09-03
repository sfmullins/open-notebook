"""Provider configuration domain model."""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import Field, SecretStr

from open_notebook.database.repository import repo_get, repo_upsert
from open_notebook.domain.base import RecordModel
from open_notebook.utils.encryption import decrypt_value, encrypt_value


class ProviderCredential:
    def __init__(
        self,
        id: str,
        name: str,
        provider: str,
        is_default: bool = False,
        api_key: Optional[SecretStr] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_version: Optional[str] = None,
        endpoint: Optional[str] = None,
        endpoint_llm: Optional[str] = None,
        endpoint_embedding: Optional[str] = None,
        endpoint_stt: Optional[str] = None,
        endpoint_tts: Optional[str] = None,
        project: Optional[str] = None,
        location: Optional[str] = None,
        credentials_path: Optional[str] = None,
        created: Optional[str] = None,
        updated: Optional[str] = None,
    ):
        self.id = id
        self.name = name
        self.provider = provider
        self.is_default = is_default
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.api_version = api_version
        self.endpoint = endpoint
        self.endpoint_llm = endpoint_llm
        self.endpoint_embedding = endpoint_embedding
        self.endpoint_stt = endpoint_stt
        self.endpoint_tts = endpoint_tts
        self.project = project
        self.location = location
        self.credentials_path = credentials_path
        self.created = created or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.updated = updated or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self, encrypted: bool = False) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "is_default": self.is_default,
            "base_url": self.base_url,
            "model": self.model,
            "api_version": self.api_version,
            "endpoint": self.endpoint,
            "endpoint_llm": self.endpoint_llm,
            "endpoint_embedding": self.endpoint_embedding,
            "endpoint_stt": self.endpoint_stt,
            "endpoint_tts": self.endpoint_tts,
            "project": self.project,
            "location": self.location,
            "credentials_path": self.credentials_path,
            "created": self.created,
            "updated": self.updated,
        }
        if self.api_key:
            secret = self.api_key.get_secret_value()
            data["api_key"] = encrypt_value(secret) if encrypted else secret
        return data

    @classmethod
    def from_dict(cls, data: dict, decrypted: bool = False) -> "ProviderCredential":
        api_key = None
        if data.get("api_key"):
            api_key = data["api_key"] if isinstance(data["api_key"], SecretStr) else SecretStr(data["api_key"])
        return cls(
            id=data["id"],
            name=data["name"],
            provider=data["provider"],
            is_default=data.get("is_default", False),
            api_key=api_key,
            base_url=data.get("base_url"),
            model=data.get("model"),
            api_version=data.get("api_version"),
            endpoint=data.get("endpoint"),
            endpoint_llm=data.get("endpoint_llm"),
            endpoint_embedding=data.get("endpoint_embedding"),
            endpoint_stt=data.get("endpoint_stt"),
            endpoint_tts=data.get("endpoint_tts"),
            project=data.get("project"),
            location=data.get("location"),
            credentials_path=data.get("credentials_path"),
            created=data.get("created"),
            updated=data.get("updated"),
        )


class ProviderConfig(RecordModel):
    record_id: ClassVar[str] = "open_notebook:provider_configs"
    credentials: Dict[str, List[ProviderCredential]] = Field(default_factory=dict)

    @classmethod
    async def get_instance(cls) -> "ProviderConfig":
        data = await repo_get(cls.record_id) or {}
        credentials: Dict[str, List[ProviderCredential]] = {}
        creds_data = data.get("credentials")
        if isinstance(creds_data, dict):
            for provider, provider_creds in creds_data.items():
                if not isinstance(provider_creds, list):
                    continue
                credentials[provider] = []
                for raw in provider_creds:
                    try:
                        cred_data = dict(raw)
                        api_key_val = cred_data.get("api_key")
                        if api_key_val and isinstance(api_key_val, str):
                            cred_data["api_key"] = SecretStr(decrypt_value(api_key_val))
                        elif api_key_val:
                            cred_data["api_key"] = SecretStr(str(api_key_val))
                        else:
                            cred_data["api_key"] = None
                        credentials[provider].append(
                            ProviderCredential(
                                id=cred_data.get("id", ""),
                                name=cred_data.get("name", "Default"),
                                provider=cred_data.get("provider", provider),
                                is_default=cred_data.get("is_default", False),
                                api_key=cred_data.get("api_key"),
                                base_url=cred_data.get("base_url"),
                                model=cred_data.get("model"),
                                api_version=cred_data.get("api_version"),
                                endpoint=cred_data.get("endpoint"),
                                endpoint_llm=cred_data.get("endpoint_llm"),
                                endpoint_embedding=cred_data.get("endpoint_embedding"),
                                endpoint_stt=cred_data.get("endpoint_stt"),
                                endpoint_tts=cred_data.get("endpoint_tts"),
                                project=cred_data.get("project"),
                                location=cred_data.get("location"),
                                credentials_path=cred_data.get("credentials_path"),
                                created=cred_data.get("created"),
                                updated=cred_data.get("updated"),
                            )
                        )
                    except Exception:
                        continue
        instance = cls.model_validate({"credentials": credentials})
        object.__setattr__(instance, "_db_loaded", True)
        return instance

    def get_default_config(self, provider: str) -> Optional[ProviderCredential]:
        credentials = self.credentials.get(provider.lower(), [])
        return next((cred for cred in credentials if cred.is_default), credentials[0] if credentials else None)

    def get_config(self, provider: str, config_id: str) -> Optional[ProviderCredential]:
        return next((cred for cred in self.credentials.get(provider.lower(), []) if cred.id == config_id), None)

    def add_config(self, provider: str, credential: ProviderCredential) -> None:
        provider_lower = provider.lower()
        credential.provider = provider_lower
        credentials = self.credentials.setdefault(provider_lower, [])
        if credentials:
            for cred in credentials:
                cred.is_default = False
            credential.is_default = True
        else:
            credential.is_default = True
        credentials.append(credential)

    def delete_config(self, provider: str, config_id: str) -> bool:
        credentials = self.credentials.get(provider.lower(), [])
        for index, cred in enumerate(credentials):
            if cred.id != config_id:
                continue
            if cred.is_default and len(credentials) > 1:
                return False
            del credentials[index]
            return True
        return False

    def set_default_config(self, provider: str, config_id: str) -> bool:
        credentials = self.credentials.get(provider.lower(), [])
        target = next((cred for cred in credentials if cred.id == config_id), None)
        if target is None:
            return False
        for cred in credentials:
            cred.is_default = False
        target.is_default = True
        target.updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return True

    def _prepare_save_data(self) -> dict:
        data: Dict[str, Any] = {"credentials": {}}
        for provider, credentials in self.credentials.items():
            data["credentials"][provider] = []
            for cred in credentials:
                cred.updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                data["credentials"][provider].append(cred.to_dict(encrypted=True))
        return data

    async def save(self) -> "ProviderConfig":
        await repo_upsert("open_notebook", self.record_id, self._prepare_save_data())
        return self

    @classmethod
    def _clear_for_test(cls) -> None:
        cls._instances.pop(cls.record_id, None)
