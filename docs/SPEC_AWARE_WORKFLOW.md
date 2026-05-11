# Spec-Aware Workflow

Praxile acts as a bridge between **Spec-Driven Development (SDD)** and **AI Coding Execution**. While SDD governs what an AI *should* build before execution, Praxile governs what the agent *actually learns* after execution.

To achieve this, Praxile is fully **Spec-Aware**. It can read, check, and verify your specifications to ensure the AI's execution aligns with human intent.

## 1. The Philosophy: WHAT over HOW

The core of a good Spec-Aware Workflow is separating intent from implementation:
- **Spec (WHAT)**: Defines the problem, success metrics, acceptance criteria, non-goals, and constraints.
- **Agent (HOW)**: Reads the Spec, explores the codebase, writes code, runs tests, and meets the criteria.

Praxile prevents "hallucinated scopes" by anchoring the AI to a concrete markdown specification.

## 2. Spec Files and Supported Formats

Praxile automatically looks for specification files in your repository. The default candidates are:
- `spec.md`
- `plan.md`
- `tasks.md`
- `constitution.md`
- `.specify/spec.md` (and other `.specify/` files)
- `docs/specs/*.md`

A high-quality Spec should contain specific sections. Praxile parses these sections using aliases (e.g., `Problem Statement`, `Success Metrics`, `Acceptance Criteria`, `Non-Goals`, `Constraints`).

## 3. Spec Quality Check (`spec check`)

Before handing a task to the agent, you can check if your specification is robust enough for an AI to execute safely:

```bash
praxile spec check --spec docs/specs/search.md
```

Praxile will evaluate the Spec based on:
1. **Completeness**: Are there measurable Success Metrics? Are Acceptance Criteria testable?
2. **Boundaries**: Are Non-Goals and Constraints explicitly defined?
3. **Over-specification**: Does the Spec contain too much "HOW" (e.g., forcing specific libraries like React/Redis without justification) instead of "WHAT"?

*Example output:*
```text
Spec quality: medium

Missing:
- Success Metrics
- Non-Goals

Risks:
- AI may over-implement because boundaries are unclear.
- The task has no measurable completion criteria.
```

## 4. Running a Task with a Spec

When dispatching a task, attach the Spec so the agent is bound to its rules:

```bash
praxile run "Implement search API" --spec docs/specs/search.md --test-command "python -m pytest"
```

During execution, Praxile injects the Spec into the `spec_context` of the runtime trajectory. The agent uses the Acceptance Criteria as its stopping condition and the Non-Goals as its boundary limits.

## 5. Post-Run Verification (`spec verify`)

After the run finishes, Praxile can verify if the agent's implementation actually complied with the Spec:

```bash
praxile spec verify latest
```

Praxile checks:
- Did the implementation cover all Acceptance Criteria?
- Did the agent violate any Non-Goals or Constraints?
- Did the execution tests cover the Success Metrics?

## 6. Silent Failure Protection

A common AI coding issue is "silent failure"—the agent completes the task without errors, but the result is fundamentally flawed. Praxile's Spec-Aware workflow mitigates this by flagging:
- **`broad_diff_without_spec`**: The agent edited files across multiple top-level directories without an attached Spec.
- **`high_complexity_change_without_plan`**: The agent made numerous edits without a corresponding `plan.md`.

These silent failure signals lower the confidence of any generated proposals and flag the run for mandatory human inspection.