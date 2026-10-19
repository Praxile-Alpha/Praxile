# Development Drafts, Not Approved Experience

MiniMax-M3.1-Flash produced these two candidates and their composition from ten
public, structured development observations. The first request passed local
schema, reference and action validation; the optional repair was not used.
The source is a seeded DOCX repair task, not an original or held-out benchmark.

`receipt.json` records the response digest, candidate digests, usage and review
limitations. `evidence.json` contains the exact public projection supplied to
the designer, not the original document or private evaluator materials.
Provenance remains caller-declared; source hashes are not independent attestation.

These files are untrusted model-generated drafts. Their claims about table
geometry are hypotheses. Fixed item counts and placeholder action examples do
not demonstrate content preservation. Never execute the examples verbatim:
they include historical hashes and illustrative text. The native worker injects
candidate context only; this bundle does not install skills or change policies.

Offline verification, with no API calls or tool execution:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -q \
  tests/unit/test_public_docx_candidate_bundle.py --override-ini=addopts=
```

This checks candidate identities, source references, declared action schemas and
activation/abstention across scopes. It does not evaluate document quality.
Independent baseline/candidate execution, content-preservation review and
composition validation are still required. No candidate has been promoted.
