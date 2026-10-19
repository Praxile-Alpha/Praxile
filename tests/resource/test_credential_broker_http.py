import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from praxile.eval.credential_broker import ChatCredentialBroker

pytestmark = [pytest.mark.resource, pytest.mark.integration, pytest.mark.timeout(20)]


def test_real_http_scoped_auth_revocation_and_cleanup():
    with ChatCredentialBroker(key="fixture-key", endpoint="https://provider.invalid/v1/chat/completions",
                              model="fixed", max_calls=2, output_budget=20, max_output=10) as broker:
        forwarded = []
        def forward(body):
            forwarded.append(json.loads(body))
            return {"choices": [{"message": {"content": "OK"}}]}
        broker._forward = forward
        host, port = broker.start()
        thread = broker._thread
        payload = json.dumps({"model": "fixed", "messages": [{"role": "user", "content": "hello"}]}).encode()
        def call(token, route="/v1/chat/completions"):
            req = Request(f"http://{host}:{port}{route}", data=payload, headers={"Authorization": "Bearer " + token})
            return urlopen(req, timeout=3)
        with pytest.raises(HTTPError) as rejected:
            call("wrong")
        assert rejected.value.code == 403
        rejected.value.close()
        with call(broker.capability) as response:
            assert json.load(response)["choices"]
        assert len(forwarded) == 1
        broker.revoke()
        with pytest.raises(HTTPError) as revoked:
            call(broker.capability)
        assert revoked.value.code == 403
        revoked.value.close()
        assert len(forwarded) == 1
    assert not thread.is_alive()
