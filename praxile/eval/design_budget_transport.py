"""One durable reservation per non-retrying candidate-design HTTP request."""
import json

from ..http_transport import SimpleHTTPTransport, HTTPTransportCancelled
from .credential_broker import SharedChatBudget


class DesignBudgetTransport(SimpleHTTPTransport):
    def __init__(self, budget, *, timeout_seconds=30):
        if not isinstance(budget, SharedChatBudget):
            raise ValueError("candidate design requires a shared chat budget")
        super().__init__(timeout_seconds=timeout_seconds, max_retries=0)
        self.budget = budget

    def post_json(self, url, *, headers=None, payload=None, timeout=None, cancel_requested=None):
        if cancel_requested and cancel_requested():
            raise HTTPTransportCancelled("design cancelled before reservation")
        if not isinstance(payload, dict) or type(payload.get("max_tokens")) is not int or payload["max_tokens"] <= 0:
            raise ValueError("budgeted design requires explicit maximum output tokens")
        raw = json.dumps(payload).encode("utf-8")
        self.budget.reserve("proposal_composer", payload["max_tokens"], request_bytes=len(raw))
        return super().post_json(url, headers=headers, payload=payload, timeout=timeout,
                                 cancel_requested=cancel_requested)

    def stream_json(self, *args, **kwargs):
        raise ValueError("budgeted candidate design requires non-streaming requests")
