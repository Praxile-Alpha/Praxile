# mini-SWE-agent Experiments

This directory contains inert, redacted evidence from explicitly requested
mini-SWE-agent experiments. It is not imported by Praxile, scanned as project
experience, or executed by the default test suite.

## Isolation Boundary

- Real A/B runs must use disposable checkouts or containers.
- Raw run state stays under the ignored `.praxile/` directory.
- Credentials, full task prompts, native payload paths, and candidate content
  must not be committed.
- Published result bundles contain reports only; they cannot modify a source
  checkout or activate an experience candidate.
- Running normal `praxile` commands does not invoke mini-SWE-agent.

Adapter and evidence-contract tests are isolated under `tests/mini_swe/` and
run only when explicitly selected:

```bash
make test-mini-swe
```

That target uses fixture subprocesses and static evidence. It does not call a
cloud model, spend API quota, download a benchmark, or launch Docker. A real
benchmark remains a manual, opt-in CLI operation with explicit isolation and
provider configuration.
