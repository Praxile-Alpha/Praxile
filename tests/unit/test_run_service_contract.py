import pytest

from praxile.services.run_service import normalize_deliverable_contract


def test_contract_normalization_detaches_nested_input():
    source = {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request",
              "outputs": [{"path": "a.json", "media_type": "application/json"}]}
    frozen = normalize_deliverable_contract(source)
    source["outputs"][0]["required"] = False
    assert "required" not in frozen["outputs"][0]
    assert normalize_deliverable_contract(None) is None


def test_contract_limit_bounds_serialized_size():
    source = {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request",
              "outputs": [{"path": "a.json", "media_type": "application/json",
                           "required_fields": ["x" * 65536]}]}
    with pytest.raises(ValueError, match="64 KiB"):
        normalize_deliverable_contract(source)
