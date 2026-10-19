import hashlib
import json

import pytest

from praxile.eval.native_worker import load_public_contract, run_native


def contract():
    return {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request",
            "outputs": [{"path": "output/report.json", "media_type": "application/json"}]}


def test_public_contract_loads_detached_and_verifies_bytes(tmp_path):
    path = tmp_path / "contract.json"
    raw = json.dumps(contract()).encode()
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    loaded = load_public_contract(path, digest)
    assert loaded == contract()
    path.write_text("{}")
    assert loaded == contract()
    with pytest.raises(ValueError, match="digest"):
        load_public_contract(path, digest)


@pytest.mark.parametrize("raw", [b"null", b'{"source":"one","source":"two"}', b"x" * 65537, b"[]"])
def test_public_contract_rejects_ambiguous_or_invalid_input(tmp_path, raw):
    path = tmp_path / "contract.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        load_public_contract(path, hashlib.sha256(raw).hexdigest())


def test_public_contract_rejects_symlink(tmp_path):
    path = tmp_path / "contract.json"
    raw = json.dumps(contract()).encode()
    path.write_bytes(raw)
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        load_public_contract(alias, hashlib.sha256(raw).hexdigest())


def test_invalid_contract_never_constructs_runtime(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("runtime must not be constructed for an invalid contract")
    monkeypatch.setattr("praxile.eval.native_worker.AgentRuntime", unexpected)
    with pytest.raises(ValueError):
        run_native(tmp_path, "public task", model="fixture", base_url="http://localhost/v1", max_steps=1,
                   deliverable_contract={"source": "private_rubric"})
