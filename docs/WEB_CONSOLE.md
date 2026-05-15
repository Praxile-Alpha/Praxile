# Praxile Web Console

Praxile Web Console is a chat-first local agent workspace with governance context.

It is served by the existing stdlib gateway:

```bash
praxile gateway serve --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

## Current Scope

The first implementation is intentionally dependency-light. It does not require Node, Vite, React, or a frontend build step. The console is rendered by `praxile.console` and talks to the local gateway through `/api/*`.

Implemented surfaces:

- chat workspace;
- session creation and message persistence;
- session retry, background run jobs, stop-request audit boundaries, and cooperative cancellation;
- local SSE event stream plus JSON polling for run job progress;
- run submission;
- chat governance cards with linked runs, reward, proposal counts, risk counts, and tool-call summaries;
- run detail with actions, readable artifacts, command/test output, diff summary, reward, loaded assets, silent risks, and generated proposals;
- proposal inbox with accept/reject actions;
- pending proposal JSON edit with confirmation and edit history;
- asset lite list/detail plus usage, graph, archive, deprecate, and reactivate actions;
- model roles, providers, route stats, selected-role route testing, and safe provider/role edits;
- Telegram/Discord channel binding configuration;
- CI / PR report viewer for local `.praxile/experience/ci` artifacts;
- multi-repo dashboard for nearby `.praxile/` project state;
- tool catalog and safety policy view with command/path checks;
- governance side panel for the latest run;
- Reflect dashboard for offline experience findings and optional proposal writing;
- graph explorer for provenance status, rebuild, and ref explanations;
- audit dashboard for project checks and redacted audit bundles;
- Spec panel for spec discovery, quality checks, and run compliance verification.

## API Shape

The gateway keeps legacy endpoints such as `/run`, `/history`, and `/accept`, and adds the chat-first API namespace:

```text
GET  /api/status
GET  /api/config
GET  /api/chat/sessions
POST /api/chat/sessions
GET  /api/chat/sessions/{session_id}
POST /api/chat/sessions/{session_id}/message
POST /api/chat/sessions/{session_id}/message-async
POST /api/chat/sessions/{session_id}/retry
POST /api/chat/sessions/{session_id}/retry-async
POST /api/chat/sessions/{session_id}/stop
GET  /api/runs
POST /api/runs
GET  /api/runs/jobs
POST /api/runs/jobs
GET  /api/runs/jobs/{job_id}
GET  /api/runs/jobs/{job_id}/events
POST /api/runs/jobs/{job_id}/cancel
GET  /api/runs/{run_id}
GET  /api/runs/{run_id}/explain
GET  /api/runs/{run_id}/trajectory
GET  /api/runs/{run_id}/reward
GET  /api/runs/{run_id}/evidence
GET  /api/runs/{run_id}/artifacts
GET  /api/runs/{run_id}/silent-failures
GET  /api/models/providers
POST /api/models/providers
PATCH /api/models/providers/{provider_id}
GET  /api/models/roles
PATCH /api/models/roles/{role}
POST /api/models/test
POST /api/models/test-all
GET  /api/channels
POST /api/channels/bind
POST /api/channels/{binding_id}/unbind
GET  /api/tools
GET  /api/safety/policy
POST /api/safety/check-command
POST /api/safety/check-path
POST /api/safety/check-tool
GET  /api/proposals
GET  /api/proposals/{proposal_id}
POST /api/proposals/{proposal_id}/edit
POST /api/proposals/{proposal_id}/accept
POST /api/proposals/{proposal_id}/reject
GET  /api/assets
GET  /api/assets/{asset_path}
GET  /api/assets/{asset_path}/usage
GET  /api/assets/{asset_path}/graph
POST /api/assets/{asset_path}/archive
POST /api/assets/{asset_path}/deprecate
POST /api/assets/{asset_path}/reactivate
GET  /api/reflect/reports
GET  /api/reflect/reports/{reflect_id}
POST /api/reflect/run
GET  /api/graph/status
GET  /api/graph/explain
POST /api/graph/rebuild
GET  /api/audit/status
POST /api/audit/check
POST /api/audit/bundle
GET  /api/ci/reports
GET  /api/ci/reports/{report_id}
GET  /api/repos
GET  /api/specs
POST /api/spec/check
POST /api/spec/verify
```

## Safety

The console does not bypass Praxile governance.

- It is local-first and should normally bind to `127.0.0.1`.
- Non-localhost binds require a token.
- Proposal accept/reject/edit actions require explicit browser confirmation or a rejection reason.
- Model provider and role edits require explicit confirmation. Raw API keys are rejected; use `api_key_env`.
- Channel binding and asset lifecycle writes require explicit confirmation.
- Background-job stop requests are cooperative. Praxile cancels at the next safe runtime boundary; it does not forcibly kill an in-flight model request from the stdlib gateway.
- CI / PR reports are read from local Praxile artifacts. The console does not fetch remote GitHub data by itself.
- API responses redact raw model API keys; they expose only environment-variable names and configured/missing status.
- Durable experience updates still flow through pending proposals.
- Pending proposal edits preserve immutable proposal identity, stay pending, and append `user_edits` history.
- Reflect can write only pending proposals. It does not mutate active memory, skills, rules, evals, or patterns directly.
- Spec verification is evidence scoring over local trajectories; failed or partial compliance does not auto-edit code.

## Roadmap

Next web-console work should focus on:

- finer-grained runtime stage events from inside the agent loop;
- force-cancelling in-flight model requests or external commands when the underlying provider/runtime supports it;
- componentized rich diff and command-output viewers;
- richer graph visualization beyond JSON explain output;
- safer structured proposal editing beyond raw JSON;
- GitHub Actions artifact import and PR comment publishing;
- packaged React/Vite frontend once the API boundary settles.
