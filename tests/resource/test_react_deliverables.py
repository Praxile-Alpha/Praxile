"""Opt-in browser UI checks against the running Vite app with fixture APIs."""
import json
import os
from urllib.parse import urlsplit

import pytest

pytestmark = [pytest.mark.resource, pytest.mark.integration,
              pytest.mark.skipif(not os.environ.get("PRAXILE_WEB_TEST_URL"), reason="explicit Vite URL required")]


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
@pytest.mark.parametrize("preservation", [None, "matched", "content_changed", "not_found", "not_checked"])
def test_contract_validation_submission_and_verification_view(tmp_path, width, height, preservation):
    from playwright.sync_api import sync_playwright, expect
    contract = {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request",
                "outputs": [{"path": "report.json", "media_type": "application/json"}]}
    path_preserved = 'output/' + 'long-project-memo-' * 5 + '.docx'
    if preservation:
        contract.update(schema_version='praxile.deliverable_contract.v3', state_equals={},
                        unchanged_files={path_preserved: 'b' * 64})
    sent = []
    def api(route):
        path = urlsplit(route.request.url).path
        result = []
        if path == "/api/status":
            result = {"project": {"root": "/fixture/project"}}
        elif path == "/api/github/context":
            result = {}
        elif path == "/api/chat/sessions":
            result = {"session_id": "demo"}
        elif path.endswith("/message-async"):
            sent.append(route.request.post_data_json)
            result = {"job": {"job_id": "job_fixture", "status": "queued"}}
        elif path == "/api/runs/jobs/job_fixture":
            result = {"status": "needs_human", "result": {
                "task_id": "task_fixture", "status": "needs_human", "deliverable_contract": contract,
                "deliverable_contract_sha256": "a" * 64,
                "deliverable_verification": {"status": "failure", "output": "Local deliverable checks completed",
                    "data": {"passed": False, "outputs": [{"path": "report.json", "status": "missing", "reason": "not_found"}]}}}}
            if preservation and preservation != 'not_checked':
                item = {'path': path_preserved, 'expected_sha256': 'b' * 64,
                        'passed': preservation == 'matched', 'reason': preservation}
                if preservation != 'not_found':
                    item['actual_sha256'] = ('b' if preservation == 'matched' else 'c') * 64
                result['result']['deliverable_verification']['data']['unchanged_files'] = [item]
        route.fulfill(json={"ok": True, "result": result})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.route("**/api/**", api)
            page.goto(os.environ["PRAXILE_WEB_TEST_URL"])
            page.get_by_label("Task", exact=True).fill("Produce report")
            page.get_by_label("Bind deliverable requirements").check()
            editor = page.get_by_label("Deliverable contract (JSON)")
            editor.fill("{invalid")
            page.get_by_role("button", name="Run", exact=True).click()
            expect(page.get_by_text("Requirements must be valid JSON.", exact=True)).to_be_visible()
            assert not sent
            editor.fill(json.dumps(contract))
            page.screenshot(path=str(tmp_path / f"composer-{width}.png"), full_page=True)
            page.get_by_role("button", name="Run", exact=True).click()
            expect(page.get_by_role("heading", name="Deliverable verification")).to_be_visible()
            expect(page.get_by_text("Not verified", exact=True).first).to_be_visible()
            expect(page.get_by_text("missing", exact=True)).to_be_visible()
            assert sent[0]["deliverable_contract"] == contract
            panel = page.get_by_role('region', name='File preservation')
            if preservation:
                expect(panel).to_be_visible()
                expect(panel.get_by_text(path_preserved, exact=True)).to_be_visible()
                expected_status = {'matched': 'Verified unchanged', 'content_changed': 'Changed'}.get(preservation, 'Not verified')
                expect(panel.get_by_text(expected_status, exact=True)).to_be_visible()
                expect(panel.get_by_text('Not checked' if preservation == 'not_checked' else preservation, exact=True)).to_be_visible()
            else:
                expect(panel).to_have_count(0)
            page.get_by_text("Frozen requirements", exact=True).click()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(tmp_path / f"verification-{width}.png"), full_page=True)
        finally:
            browser.close()
