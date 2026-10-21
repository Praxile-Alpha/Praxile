import pytest

from praxile.cli import main
from praxile.config import Config
from praxile.services.proposal_service import ProposalService
from praxile.services.errors import ServiceError
from praxile.store import ExperienceStore

pytestmark = pytest.mark.resource


def test_preference_cli_review_retrieve_and_rollback(tmp_path):
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    assert main(["--project", str(tmp_path), "memory", "propose-preference", "brief",
                 "Prefer concise zebrabrief summaries", "--applies-when", "Final summaries",
                 "--source", "Explicit local user request"]) == 0
    pending = [p for p in store.list_proposals(status="pending") if p.get("experience_purpose") == "personalization"]
    assert len(pending) == 1
    target = config.paths.state / "memory/preferences/brief.md"
    assert not target.exists()
    service = ProposalService(store)
    with pytest.raises(ServiceError):
        service.accept(pending[0]["proposal_id"])
    assert not target.exists()
    service.accept(pending[0]["proposal_id"], confirm=True)
    assert target.is_file()
    assert "personalization" in target.read_text()
    assert any(row["path"].endswith("memory/preferences/brief.md")
               for row in store.retrieve("zebrabrief", kinds=["memory"]))
    store.rollback_proposal(pending[0]["proposal_id"])
    assert not target.exists()


@pytest.mark.parametrize("outcome, level", [("success", "strong_positive"), ("failed", "harmful")])
def test_preference_never_receives_task_outcome_credit(tmp_path, outcome, level):
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    path = ".praxile/memory/preferences/brief.md"
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Preferred brief summaries")
    store.index_asset(target)
    store.record_asset_usage("task_pref", [{"path": path, "score": 1}], used_in_prompt=True)
    store.update_asset_usage_outcome("task_pref", outcome, referenced_paths=[path], attribution_results=[{
        "path": path, "attribution_level": level, "should_update_asset_outcome": True,
        "referenced": True, "used_explicitly": True, "confidence": 1,
    }])
    asset = store.get_asset(path)
    assert asset["positive_outcome_count"] == asset["negative_outcome_count"] == 0
    usage = store.usage_for_task("task_pref")
    assert usage[0]["referenced"]
    assert usage[0]["semantic_attribution"]["capability_credit_excluded"] is True
    assert usage[0]["semantic_attribution"]["should_update_asset_outcome"] is False
    assert all(event["stage"] != "outcome_attributed" for event in store.activation_events_for_task("task_pref"))
