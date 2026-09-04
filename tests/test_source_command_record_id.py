"""Regression tests for PostgreSQL source command references."""

from open_notebook.database.record_id import RecordID
from open_notebook.domain.notebook import Source


def test_source_command_survives_strict_model_dump_round_trip():
    """ObjectModel.save() strictly revalidates model_dump(); RecordID must rehydrate."""
    source = Source(id="source:123", title="Test", command="command:456")

    dumped = source.model_dump()
    assert dumped["command"] == {"table": "command", "id": "456"}

    validated = Source.model_validate(dumped, strict=True)
    assert isinstance(validated.command, RecordID)
    assert str(validated.command) == "command:456"


def test_source_prepare_save_data_preserves_command_reference():
    """The PostgreSQL save payload keeps the command reference as one RecordID."""
    source = Source(id="source:123", title="Test", command="command:456")

    data = source._prepare_save_data()

    assert isinstance(data["command"], RecordID)
    assert str(data["command"]) == "command:456"
