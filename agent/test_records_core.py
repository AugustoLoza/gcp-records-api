import pytest

from records_core import InMemoryStorage, ValidationError, validate_payload


def test_validate_payload_returns_record_with_fields():
    record = validate_payload({"type": "blood_test", "value": 120, "unit": "mg/dL"})
    assert record.type == "blood_test"
    assert record.value == 120
    assert record.unit == "mg/dL"
    assert record.id


def test_validate_payload_generates_unique_ids():
    a = validate_payload({"type": "x", "value": 1, "unit": "u"})
    b = validate_payload({"type": "x", "value": 1, "unit": "u"})
    assert a.id != b.id


@pytest.mark.parametrize(
    "payload",
    [
        {"value": 120, "unit": "mg/dL"},  # missing type
        {"type": "blood_test", "unit": "mg/dL"},  # missing value
        {"type": "blood_test", "value": 120},  # missing unit
        {"type": "", "value": 120, "unit": "mg/dL"},  # empty type
        {"type": "blood_test", "value": "abc", "unit": "mg/dL"},  # non-numeric value
    ],
)
def test_validate_payload_rejects_invalid_input(payload):
    with pytest.raises(ValidationError):
        validate_payload(payload)


def test_storage_get_missing_returns_none():
    storage = InMemoryStorage()
    assert storage.get("does-not-exist") is None


def test_storage_upsert_then_get_roundtrips():
    storage = InMemoryStorage()
    record = validate_payload({"type": "blood_test", "value": 120, "unit": "mg/dL"})
    inserted = storage.upsert(record)
    assert inserted is True
    assert storage.get(record.id) == record


def test_storage_upsert_is_idempotent():
    storage = InMemoryStorage()
    record = validate_payload({"type": "blood_test", "value": 120, "unit": "mg/dL"})
    first = storage.upsert(record)
    second = storage.upsert(record)
    assert first is True
    assert second is False
    assert storage.get(record.id) == record
