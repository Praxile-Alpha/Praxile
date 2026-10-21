import hashlib
import json

import pytest

from praxile.eval.native_run_claim import NativeRunClaim


def complete(tmp_path):
    claim = NativeRunClaim(tmp_path, "one", {"model": "fixture"})
    claim.acquire()
    lease = tmp_path / ("harbor-lease-" + "a" * 32)
    (lease / "agent").mkdir(parents=True)
    (lease / "outputs").mkdir()
    report = lease / "execution.json"
    report.write_text(json.dumps({"status": "executor_returned", "cleanup_verified": True,
                                 "required_outputs_collected": True}))
    (lease / "agent/praxile-result.json").write_text("{}")
    (lease / "outputs/answer.txt").write_text("answer")
    claim.bind_report(report)
    files = {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (report, lease / "agent/praxile-result.json", lease / "outputs/answer.txt")}
    claim.complete(files)
    return claim, report


def test_exclusive_id_and_verified_terminal_reuse(tmp_path):
    claim, report = complete(tmp_path)
    second = NativeRunClaim(tmp_path, "one", {"model": "fixture"})
    with pytest.raises(FileExistsError):
        second.acquire()
    assert second.resume() == report
    with pytest.raises(ValueError, match="frozen execution plan"):
        NativeRunClaim(tmp_path, "one", {"model": "changed"}).resume()


def test_interrupted_claim_never_replays(tmp_path):
    NativeRunClaim(tmp_path, "one", {}).acquire()
    with pytest.raises(ValueError, match="automatic replay forbidden"):
        NativeRunClaim(tmp_path, "one", {}).resume()


@pytest.mark.parametrize("relative", ["execution.json", "agent/praxile-result.json", "outputs/answer.txt"])
def test_corruption_refuses_reuse(tmp_path, relative):
    claim, report = complete(tmp_path)
    (report.parent / relative).write_text("changed")
    with pytest.raises(ValueError, match="evidence changed"):
        claim.resume()


def test_symlink_and_unsafe_id_refused(tmp_path):
    with pytest.raises(ValueError):
        NativeRunClaim(tmp_path, "../escape", {})
    claim, report = complete(tmp_path)
    artifact = report.parent / "outputs/answer.txt"
    artifact.unlink()
    artifact.symlink_to(report)
    with pytest.raises(ValueError):
        claim.resume()


def test_changed_evidence_cannot_be_sealed(tmp_path):
    claim, report = complete(tmp_path)
    record = json.loads(claim.path.read_text())
    (report.parent / "outputs/answer.txt").write_text("changed")
    with pytest.raises(ValueError, match="before sealing"):
        claim.complete(record["files"])
