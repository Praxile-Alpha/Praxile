"""Explicit public development review through a shared-quota credential broker."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from .content_review import content_response_contract, output_units
from .content_review_batch import plan_content_review_batch, review_content_batched
from .credential_broker import ChatCredentialBroker, SharedChatBudget, read_private_key
from ..model import OpenAICompatibleProvider, ModelUnavailable
from ..trajectory import TrajectoryLogger
from ..utils import write_json


class _BrokerTransport:
    def __init__(self, broker):
        self.broker = broker

    def post_json(self, url, *, headers, payload, timeout, cancel_requested=None):
        if cancel_requested is not None and cancel_requested():
            raise ModelUnavailable("judge request cancelled before admission")
        return self.broker.chat("Bearer " + self.broker.capability, payload)


class _BrokerProvider(OpenAICompatibleProvider):
    def _api_key(self):
        # The transport owns the capability; never touch process-global credentials.
        return None


class _JudgeRouter:
    def __init__(self, broker):
        self.broker = broker
        self.provider = _BrokerProvider("broker_judge", {"base_url": "http://127.0.0.1/v1",
            "models": [{"name": broker.model, "supports_tools": True}]}, transport=_BrokerTransport(broker))

    def chat(self, messages, *, purpose, private, high_risk, **options):
        if purpose != "content_review_judge" or private is not False or high_risk is not False:
            raise ValueError("broker judge only accepts explicit public development review")
        started = time.monotonic()
        response = self.provider.chat({"model": self.broker.model, "messages": messages, **options})
        response["route"] = {"provider": "broker_judge", "model": self.broker.model, "purpose": purpose}
        response["latency_ms"] = (time.monotonic() - started) * 1000
        return response


def run_broker_content_review(*, directory, inputs, credential_file, endpoint, model, shared_budget,
                             total_output_tokens, public=False, batch_size=4, timeout_per_call=90,
                             request_options=None):
    """No fallback, transport retry, key in config, automatic replay or promotion.

    Caller supplies extracted public text and development requirements, not a
    hidden official rubric. This does not authenticate extraction or task quality.
    """
    if public is not True or not isinstance(shared_budget, SharedChatBudget):
        raise ValueError("explicit public inputs and shared judge budget required")
    if not isinstance(inputs, dict) or set(inputs) != {"source_text", "output_text", "requirements"}:
        raise ValueError("invalid review input fields")
    inputs = deepcopy(inputs)
    options = dict(total_output_tokens=total_output_tokens, batch_size=batch_size,
        timeout_per_call=timeout_per_call, private=False, response_mode="function", citation_mode="units")
    plan = plan_content_review_batch(**inputs, **options)
    units = [unit["id"] for unit in output_units(inputs["output_text"])]
    contracts = [content_response_contract(group["ids"], units) for group in plan["groups"]]
    key = read_private_key(Path(credential_file))
    broker = ChatCredentialBroker(key=key, endpoint=endpoint, model=model,
        max_calls=len(contracts), output_budget=total_output_tokens,
        max_output=max(group["max_tokens"] for group in plan["groups"]),
        ttl_seconds=min(3600, len(contracts) * timeout_per_call + 30), request_timeout=timeout_per_call,
        request_options=request_options, response_contracts=contracts, shared_budget=shared_budget, budget_role="judge")
    root = Path(directory).absolute()
    logger = TrajectoryLogger("Public development broker content review", {})
    report = {"status": "pending", "promotion_eligible": False, "automatic_replay_allowed": False}
    claimed = False
    try:
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        claimed = True
        manifest = {"schema_version": "praxile.broker_content_review.v1", "model": model,
            "endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest(), "plan": plan,
            "request_options": deepcopy(request_options or {}),
            "contract_sha256": [hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() for value in contracts],
            "shared_budget_start": shared_budget.status(), "official_evaluation": False, "promotion_eligible": False}
        write_json(root / "manifest.json", manifest)
        write_json(root / "inputs.json", inputs)
        write_json(root / "report.json", report)
        def progress(value):
            write_json(root / "trajectory.json", logger.data)
            write_json(root / "report.json", value)
        report = review_content_batched(_JudgeRouter(broker), logger, **inputs, **options, progress_checkpoint=progress)
    except BaseException as error:
        batches = logger.data.get("content_review_batches", [])
        report = {**(batches[-1] if batches else report), "status": "interrupted_or_failed",
                  "error_type": type(error).__name__, "promotion_eligible": False}
        raise
    finally:
        broker.revoke()
        broker.close()
        if claimed:
            write_json(root / "trajectory.json", logger.data)
            write_json(root / "report.json", report)
            write_json(root / "broker-usage.json", broker.status())
    return root / "report.json"
