# Praxile Web Console

This is the optional React/Vite frontend for Praxile's local gateway.

The Python package still ships a dependency-free stdlib console. This app is a richer standalone UI that talks to the same `/api/*` gateway endpoints.

## Deliverable Requirements

The Chat composer can bind an inline V1/V2 deliverable contract to a new task.
JSON syntax, envelope and size are checked before submission; the gateway remains
authoritative for full validation. A failed submission retains the draft. Running
jobs are polled until completion, and the run view displays local file/state checks,
the frozen contract and its digest separately from official evaluation.

Opt-in Playwright checks use fixture APIs, not model calls:

```bash
PRAXILE_WEB_TEST_URL=http://127.0.0.1:5173 python -B -m pytest -q \
  tests/resource/test_react_deliverables.py --forked -o addopts=''
```

Run the test from the repository root while Vite is running. The test covers desktop
and mobile layouts, malformed JSON, request payloads and asynchronous result display.
The stdlib console does not yet expose this input control.

## Develop

```bash
praxile gateway serve --host 127.0.0.1 --port 8765
cd web
npm install
npm run dev
```

Set `VITE_PRAXILE_API_BASE=http://127.0.0.1:8765` if the frontend is served from a different origin.

Remote GitHub actions still require explicit confirmation and a token environment variable such as `GITHUB_TOKEN` on the gateway process.
