import re
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Type, TypeVar, Union, cast

from loguru import logger
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from open_notebook.database.repository import (
    repo_create,
    repo_delete,
    repo_get,
    repo_list,
    repo_relate,
    repo_update,
    repo_upsert,
)
from open_notebook.exceptions import (
    DatabaseOperationError,
    InvalidInputError,
    NotFoundError,
)

T = TypeVar("T", bound="ObjectModel")


class ObjectModel(BaseModel):
    id: Optional[str] = None
    table_name: ClassVar[str] = ""
    nullable_fields: ClassVar[set[str]] = set()
    created: Optional[datetime] = None
    updated: Optional[datetime] = None

    @classmethod
    def _validate_order_by(cls, order_by: str) -> str:
        """Validate and normalize a repository ordering expression."""
        allowed_field_pattern = re.compile(r"^[a-z_][a-z0-9_]*$")
        allowed_directions = {"asc", "desc"}
        clauses = [c.strip() for c in order_by.split(",")]
        validated_clauses = []
        for clause in clauses:
            parts = clause.strip().split()
            if len(parts) == 1:
                if not allowed_field_pattern.match(parts[0].lower()):
                    raise InvalidInputError(f"Invalid order_by field: '{parts[0]}'")
                validated_clauses.append(parts[0].lower())
            elif len(parts) == 2:
                if not allowed_field_pattern.match(parts[0].lower()) or parts[1].lower() not in allowed_directions:
                    raise InvalidInputError(f"Invalid order_by clause: '{clause.strip()}'")
                validated_clauses.append(f"{parts[0].lower()} {parts[1].lower()}")
            else:
                raise InvalidInputError(f"Invalid order_by clause: '{clause.strip()}'")
        return ", ".join(validated_clauses)

    @classmethod
    async def get_all(cls: Type[T], order_by=None) -> List[T]:
        try:
            if not cls.table_name:
                raise InvalidInputError("get_all() must be called from a specific model class")
            target_class = cls
            field = None
            descending = False
            if order_by:
                validated = cls._validate_order_by(order_by)
                first = validated.split(",", 1)[0].split()
                field = first[0]
                descending = len(first) == 2 and first[1] == "desc"
            result = await repo_list(cls.table_name, order_by=field, descending=descending)
            objects = []
            for obj in result:
                try:
                    objects.append(target_class(**obj))
                except Exception as e:
                    logger.critical(f"Error creating object: {str(e)}")
            return objects
        except Exception as e:
            logger.error(f"Error fetching all {cls.table_name}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    @classmethod
    async def get(cls: Type[T], id: str) -> T:
        if not id:
            raise InvalidInputError("ID cannot be empty")
        try:
            table_name = id.split(":")[0] if ":" in id else id
            if cls.table_name and cls.table_name == table_name:
                target_class: Type[T] = cls
            else:
                found_class = cls._get_class_by_table_name(table_name)
                if not found_class:
                    raise InvalidInputError(f"No class found for table {table_name}")
                target_class = cast(Type[T], found_class)
            result = await repo_get(id)
            if result:
                return target_class(**result)
            raise NotFoundError(f"{table_name} with id {id} not found")
        except Exception as e:
            logger.error(f"Error fetching object with id {id}: {str(e)}")
            logger.exception(e)
            raise NotFoundError(f"Object with id {id} not found - {str(e)}")

    @classmethod
    def _get_class_by_table_name(cls, table_name: str) -> Optional[Type["ObjectModel"]]:
        def get_all_subclasses(c: Type["ObjectModel"]) -> List[Type["ObjectModel"]]:
            all_subclasses: List[Type["ObjectModel"]] = []
            for subclass in c.__subclasses__():
                all_subclasses.append(subclass)
                all_subclasses.extend(get_all_subclasses(subclass))
            return all_subclasses

        for subclass in get_all_subclasses(ObjectModel):
            if hasattr(subclass, "table_name") and subclass.table_name == table_name:
                return subclass
        return None

    async def save(self) -> None:
        """Save the model to PostgreSQL without generating embeddings inline."""
        try:
            self.model_validate(self.model_dump(), strict=True)
            data = self._prepare_save_data()
            data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            repo_result: Union[List[Dict[str, Any]], Dict[str, Any]]
            if self.id is None:
                data["created"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                repo_result = await repo_create(self.__class__.table_name, data)
            else:
                data["created"] = (
                    self.created.strftime("%Y-%m-%d %H:%M:%S")
                    if isinstance(self.created, datetime)
                    else self.created
                )
                logger.debug(f"Updating record with id {self.id}")
                repo_result = await repo_update(self.__class__.table_name, self.id, data)
            result_list: List[Dict[str, Any]] = repo_result if isinstance(repo_result, list) else [repo_result]
            for key, value in result_list[0].items():
                if hasattr(self, key):
                    if isinstance(getattr(self, key), BaseModel):
                        setattr(self, key, type(getattr(self, key))(**value))
                    else:
                        setattr(self, key, value)
        except ValidationError as e:
            logger.error(f"Validation failed: {e}")
            raise
        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"Error saving record: {e}")
            raise DatabaseOperationError(e)

    def _prepare_save_data(self) -> Dict[str, Any]:
        data = self.model_dump()
        return {key: value for key, value in data.items() if value is not None or key in self.__class__.nullable_fields}

    async def delete(self) -> bool:
        if self.id is None:
            raise InvalidInputError("Cannot delete object without an ID")
        try:
            logger.debug(f"Deleting record with id {self.id}")
            return await repo_delete(self.id)
        except Exception as e:
            logger.error(f"Error deleting {self.__class__.table_name} with id {self.id}: {str(e)}")
            raise DatabaseOperationError(f"Failed to delete {self.__class__.table_name}")

    async def relate(self, relationship: str, target_id: str, data: Optional[Dict] = None) -> Any:
        if not relationship or not target_id or not self.id:
            raise InvalidInputError("Relationship and target ID must be provided")
        try:
            return await repo_relate(source=self.id, relationship=relationship, target=target_id, data=data or {})
        except Exception as e:
            logger.error(f"Error creating relationship: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    @field_validator("created", "updated", mode="before")
    @classmethod
    def parse_datetime(cls, value):
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class RecordModel(BaseModel):
    model_config = ConfigDict(
        validate_assignment=True,
        arbitrary_types_allowed=True,
        extra="allow",
        from_attributes=True,
        defer_build=True,
    )
    record_id: ClassVar[str]
    auto_save: ClassVar[bool] = False
    _instances: ClassVar[Dict[str, "RecordModel"]] = {}

    def __new__(cls, **kwargs):
        if cls.record_id in cls._instances:
            instance = cls._instances[cls.record_id]
            if kwargs:
                for key, value in kwargs.items():
                    setattr(instance, key, value)
            return instance
        instance = super().__new__(cls)
        cls._instances[cls.record_id] = instance
        return instance

    def __init__(self, **kwargs):
        if not hasattr(self, "_initialized"):
            object.__setattr__(self, "__dict__", {})
            super().__init__(**kwargs)
            object.__setattr__(self, "_initialized", True)
            object.__setattr__(self, "_db_loaded", False)

    async def _load_from_db(self):
        if not getattr(self, "_db_loaded", False):
            row = await repo_get(self.record_id)
            if row:
                for key, value in row.items():
                    if hasattr(self, key):
                        object.__setattr__(self, key, value)
            object.__setattr__(self, "_db_loaded", True)

    @classmethod
    async def get_instance(cls) -> "RecordModel":
        instance = cls()
        await instance._load_from_db()
        return instance

    @model_validator(mode="after")
    def auto_save_validator(self):
        if self.__class__.auto_save:
            logger.warning(
                f"Auto-save is enabled for {self.__class__.__name__} but update() is async. Call await instance.update() manually."
            )
        return self

    async def update(self):
        data = {
            field_name: getattr(self, field_name)
            for field_name, field_info in self.model_fields.items()
            if not str(field_info.annotation).startswith("typing.ClassVar")
        }
        await repo_upsert(
            self.__class__.table_name if hasattr(self.__class__, "table_name") else "record",
            self.record_id,
            data,
        )
        row = await repo_get(self.record_id)
        if row:
            for key, value in row.items():
                if hasattr(self, key):
                    object.__setattr__(self, key, value)
        return self

    @classmethod
    def clear_instance(cls):
        if cls.record_id in cls._instances:
            del cls._instances[cls.record_id]

    async def patch(self, model_dict: dict):
        for key, value in model_dict.items():
            setattr(self, key, value)
        await self.update()
