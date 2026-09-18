# P2-A: Capability Goal and Eval Firewall

P2-A adapts the hidden-final-evaluation lesson of [ASPIRE](https://arxiv.org/abs/2608.31111) to Praxile's coding-agent control plane. The agent may optimize local proxy signals; Praxile records the hypothesis separately and never treats a proxy gain as a held-out capability gain.

## Implemented

- [x] Versioned `CapabilityGoal`, `OperationalizationHypothesis`, `EvaluationContract`, `InformationBoundary`, and `TerminalSelectionRule` records with strict JSON parsing and a content digest.
- [x] `praxile eval capability-check` validates the protocol without starting a model.
- [x] `praxile eval ab --capability-protocol` freezes the protocol in the immutable experiment manifest; changing it requires a new experiment ID.
- [x] Before either arm runs, validate that every selected task is assigned to development or held-out, partitions do not overlap, the declared control-plane evaluator name matches the actual evaluator identity, and task metadata/candidate context do not carry named evaluator fields or reference/test patches.
- [x] The protocol must declare `isolation_level: logical_only`. Declaring `os_sandbox` fails closed until such isolation is implemented.
- [x] Agent-authored, declarative `ProxyEvalProposal` with versioned checks over development-task deltas. `proxy-propose`, `proxy-review`, interactive `proxy-approve`, and `proxy-run` support explicit review before execution; arbitrary executable scorers are deliberately not accepted.
- [x] A/B reports and public exports distinguish approved local proxy results from held-out objective transitions. Unknown outcomes abstain; proxy-only improvements are inconclusive; objective gains still require human review and the existing promotion gates.
- [x] A one-use held-out claim binds the task set to one candidate and experiment. A second experiment with any overlapping held-out task ID is rejected; resume after finalization only re-analyzes frozen results. Diagnose, re-analysis, proxy-run, and public export APIs refuse intermediate held-out feedback.
- [x] Legacy A/B runs without a capability protocol remain readable and unchanged.
- [ ] Arbitrary agent-authored executable proxy-eval suites. Deliberately excluded from the safe declarative proxy format; adding them requires separate command and scorer sandbox policy.
- [ ] OS-level or remote isolation of held-out evaluator storage. The current local adapter runs on the same host; these checks form a **logical information boundary**, not a security sandbox against a malicious process with host filesystem access.
- [ ] Multi-task statistical selection policy. A one-task demo can open human review, but cannot substantiate a general capability claim.

## Run

Edit copies of [the capability protocol](candidates/P2_A_CAPABILITY_PROTOCOL.example.json) and [proxy eval proposal](candidates/P2_A_PROXY_EVAL.example.json) so their task IDs match the selected development and held-out sets. First collect development-only A/B evidence (real model calls):

```bash
praxile eval ab docs/candidates/P0_C_BOUNDED_INVESTIGATION.json \
  --development-only \
  --experiment-id p2-a-dev-example \
  --dataset-name SWE-bench/SWE-bench_Lite \
  --instance-id sympy__sympy-16281 \
  --model openai/MiniMax-M3 \
  --cost-tracking ignore_errors \
  --timeout 1800 \
  --step-limit 150
```

Register, inspect, and explicitly approve the agent-designed proxy before using it. Approval prompts for the exact ID/version in an interactive terminal:

```bash
praxile eval proxy-propose docs/candidates/P2_A_PROXY_EVAL.example.json
praxile eval proxy-review focused-investigation-efficiency --version 1
praxile eval proxy-approve focused-investigation-efficiency --version 1 --reviewer YOUR_NAME
praxile eval proxy-run focused-investigation-efficiency --version 1 \
  --experiment-id p2-a-dev-example --json
```

Freeze the final protocol and run the control-plane held-out A/B. The proxy result can be included, but cannot override the held-out resolution outcome:

```bash
praxile eval capability-check docs/candidates/P2_A_CAPABILITY_PROTOCOL.example.json --json
praxile eval ab docs/candidates/P0_C_BOUNDED_INVESTIGATION.json \
  --capability-protocol docs/candidates/P2_A_CAPABILITY_PROTOCOL.example.json \
  --proxy-eval focused-investigation-efficiency@1 \
  --experiment-id p2-a-final-example \
  --dataset-name SWE-bench/SWE-bench_Lite \
  --instance-id sympy__sympy-16281 \
  --instance-id sympy__sympy-18532 \
  --model openai/MiniMax-M3 \
  --cost-tracking ignore_errors \
  --timeout 1800 \
  --step-limit 150
praxile eval ab-analyze p2-a-final-example --json
```

The A/B commands make real model calls and require a configured MiniMax endpoint, model quota, Docker, mini-SWE-agent, and SWE-bench. `capability-check` and proposal review are offline. These two SymPy instances have appeared in previous public Praxile experiments, so this is an **API demonstration, not fresh held-out evidence**. For credible promotion evidence, pre-register several genuinely unseen tasks. Once a held-out set has been claimed, a different experiment in the same project cannot reuse it by changing the candidate or contract ID.

The experiment's `capability.terminal_selection` is **not** a promotion command. It reports `blocked`, `inconclusive`, or `human_review_required`; activation still follows Praxile's existing approval and safety gates. Protocol manifests contain IDs and policy text, never expected answers, reference patches, or full hidden rubrics. The private benchmark evaluator runs after each adapter run and its feedback is not passed through the adapter task/policy projection. Because local processes share a host, the same-UID executor can still read host files directly; do not use this mode for adversarial secrecy requirements until OS/container or remote evaluator isolation is implemented.
