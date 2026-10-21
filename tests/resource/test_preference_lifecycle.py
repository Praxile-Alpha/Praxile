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
