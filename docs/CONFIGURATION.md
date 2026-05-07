# Configuration

Praxile uses project-local JSON config at:

```text
.praxile/config.json
```

The file is JSONC-compatible JSON, not YAML. Comments and trailing commas are accepted when reading hand-edited config, but Praxile writes strict JSON. Bot tokens and model API keys should stay in environment variables; config stores only environment variable names.

Fresh project config is intentionally clean. `praxile init` creates no model providers and no author-chosen model names. Run `praxile setup` or `praxile init --wizard` to configure Ollama, an OpenAI-compatible endpoint, Anthropic, and optional Telegram/Discord bindings step by step.

See the repository-level example:

```text
praxile.config.example.json
```

## Init Detection

`praxile init` inspects common project markers and writes detection metadata into config for the current repository:

```json
{
  "project": {
    "detected_stacks": ["node", "react", "vite"],
    "detected_markers": ["package.json"],
    "detected_package_manager": "npm",
    "detected_test_commands": ["npm test", "npm run build"]
  },
  "runtime": {
    "default_test_commands": ["npm test", "npm run build"]
  }
}
```

Supported first-pass detection includes Python, Node/React/Vite/Next, Go, and Rust. Override detection with:

```bash
praxile init --test-command "python -m pytest"
praxile init --no-detect
```

`praxile doctor` compares configured, suggested, and active verification commands and reports missing local tools.

## Runtime, Search, And Index

Praxile keeps operational knobs in `.praxile/config.json`:

```json
{
  "runtime": {
    "model_timeout_seconds": 30,
    "online_check_timeout_seconds": 8,
    "shell_timeout_seconds": 120,
    "test_timeout_seconds": 180,
    "action_parse_retries": 2
  },
  "shell": {
    "allow_shell_features": false
  },
  "checkpoint": {
    "enabled": true,
    "every_steps": 1
  },
  "context": {
    "compression_enabled": true,
    "max_prompt_chars": 120000,
    "compression_threshold": 0.8,
    "observation_keep_chars": 1600,
    "recent_messages_to_keep": 6
  },
  "trace": {
    "enabled": true,
    "sync": false,
    "retention_days": 30
  },
  "task_analysis": {
    "llm_assisted": false,
    "llm_model_role": "cheap_model",
    "llm_timeout_seconds": 12,
    "llm_max_tokens": 800
  },
  "semantic_judges": {
    "enabled": false,
    "local_first": true,
    "max_calls_per_run": 5,
    "max_calls_per_mine_patterns": 20,
    "feedback_classifier": {
      "enabled": true,
      "role": "feedback_classifier",
      "use_for_complex_feedback_only": true
    },
    "attribution_judge": {
      "enabled": true,
      "role": "attribution_judge",
      "only_for_loaded_assets_with_score_above": 0.5
    },
    "pattern_mining": {
      "enabled": true,
      "role": "pattern_mining",
      "only_after_heuristic_score": 0.45
    },
    "counterexample_checker": {
      "enabled": true,
      "role": "counterexample_checker"
    }
  },
  "search": {
    "backend": "auto",
    "timeout_seconds": 30,
    "default_limit": 80
  },
  "project_map": {
    "cache_enabled": true,
    "cache_ttl_seconds": 30
  },
  "index": {
    "fts_enabled": true
  },
  "retrieval": {
    "hybrid_enabled": false,
    "vector_enabled": false,
    "vector_provider": "local_hash",
    "embedding_provider": "local_hash",
    "embedding_model": null,
    "vector_dims": 256,
    "vector_min_score": 0.05,
    "keyword_priority_weight": 0.05,
    "vector_priority_weight": 0.03,
    "hybrid_rank_boost": 1.0,
    "usage_log_weight": 0.02,
    "positive_outcome_weight": 0.1,
    "negative_outcome_weight": 0.2,
    "stale_usage_days": 90,
    "stale_usage_penalty": 0.1
  },
  "evolution": {
    "llm_assisted_proposals": false,
    "llm_model_role": "evolution_model",
    "llm_timeout_seconds": 20,
    "llm_max_tokens": 1800,
    "consolidation_min_duplicates": 2,
    "consolidation_stale_days": 90,
    "consolidation_low_value_max_confidence": 0.4,
    "rejection_suppression_threshold": 2
  },
  "memory": {
    "shard_enabled": true,
    "project_memory_soft_limit_bytes": 200000,
    "shard_by": "month"
  },
  "browser": {
    "enabled": false,
    "adapter": "playwright",
    "dev_server_url": null,
    "artifact_dir": ".praxile/experience/artifacts",
    "allowed_hosts": ["localhost", "127.0.0.1", "::1"],
    "timeout_ms": 15000,
    "viewport_width": 1280,
    "viewport_height": 900,
    "current_mvp": "playwright_optional_with_human_acceptance"
  },
  "architecture_gate": {
    "shadow_mode": false
  },
  "interop_guardrails": {
    "refuse_writes_when_external_agent_detected": true,
    "lock_files": [".hermes/agent.lock", ".openclaw/agent.lock", ".codex/agent.lock", ".agent.lock"],
    "environment_flags": ["HERMES_AGENT_ACTIVE", "OPENCLAW_AGENT_ACTIVE", "PRAXILE_EXTERNAL_AGENT_ACTIVE"]
  },
  "safety": {
    "backup_max_files": 500,
    "backup_max_bytes": 200000000
  },
  "model_roles": {
    "coding_agent": {
      "provider": "openai_compatible",
      "model": "your-coding-model",
      "fallback": [{"provider": "local_ollama", "model": "qwen2.5-coder:7b"}]
    },
    "evidence_extraction": {
      "provider": "local_ollama",
      "model": "qwen2.5-coder:7b"
    },
    "experience_reflection": {
      "provider": "local_ollama",
      "model": "qwen2.5-coder:7b"
    },
    "reward_judge": {
      "provider": "local_ollama",
      "model": "qwen2.5-coder:7b",
      "mode": "optional"
    },
    "proposal_composer": {
      "provider": "local_ollama",
      "model": "qwen2.5-coder:7b"
    },
    "review_recommendation": {
      "provider": "local_ollama",
      "model": "qwen2.5-coder:7b"
    },
    "embedding": {
      "provider": "local",
      "model": "local_hash"
    }
  },
  "cost_control": {
    "local_first": true,
    "prefer_ollama_for": [
      "evidence_extraction",
      "experience_reflection",
      "reward_judge",
      "proposal_composer",
      "review_recommendation",
      "feedback_classifier",
      "counterexample_checker",
      "pattern_mining",
      "project_pattern_composer"
    ],
    "use_cloud_for": ["coding_agent", "deep_project_pattern_mining"],
    "max_cloud_calls_per_run": 3,
    "max_cost_per_run_usd": 0.05
  },
  "reward": {
    "mode": "hybrid",
    "weights": {
      "objective": 0.60,
      "user_feedback": 0.30,
      "llm_judge": 0.10,
      "task_success": 0.30,
      "process_safety": 0.20,
      "regression": 0.25,
      "cost": 0.10,
      "experience_value": 0.15
    },
    "user_feedback": {
      "enabled": true,
      "collect_from_chat": true,
      "default_target": "latest_run",
      "require_confirmation_for_asset_negative_feedback": true
    },
    "llm_judge": {
      "enabled": false,
      "role": "reward_judge",
      "max_cost_per_run": 0.01,
      "timeout_seconds": 12,
      "max_tokens": 800
    },
    "cost_thresholds": {
      "medium_tool_calls": 12,
      "high_tool_calls": 20,
      "medium_model_calls": 8,
      "high_model_calls": 12
    }
  }
}
```

`search.backend=auto` prefers `rg` when available and falls back to Python search. Both paths apply sensitive-file protection and return skipped/error statistics so review can show incomplete search coverage.

Project map caching stores a short-lived summary in `.praxile/cache/project_map.json`. Tool calls can force a fresh scan with `{"type":"project_map","refresh":true}` when recent filesystem changes matter.

`checkpoint.enabled=true` writes resumable execution state under `.praxile/checkpoints/` after important runtime steps. Use `praxile run --resume <TASK_ID>` to continue from the last persisted action after an interruption.

`context.compression_enabled=true` deterministically compresses older observations when prompt text approaches the configured threshold. This keeps long action loops from dragging full command output or large file reads through every subsequent model call.

`trace.enabled=true` writes structured runtime events to daily files such as `.praxile/logs/trace_20260504.jsonl`, including model request/response timing, tool actions, snapshot refreshes, checkpoint writes, and context compression events. `trace.sync=false` avoids a disk `fsync` on every trace line; set it to `true` only when hard-sync audit durability is more important than runtime latency. `trace.retention_days` removes old trace files during runtime startup.

`read_file` supports `start_line` and `end_line` for long files, so truncation is not a permanent context hole. Range reads return line numbers plus `has_more_before` and `has_more_after` metadata.

`praxile index status` reports indexed assets, FTS availability, queued index events, and vector rows without performing an implicit deep scan. Use `praxile index status --scan` when you explicitly want missing/stale detection. `praxile index watch --once` scans mtime/size, queues changed assets, and processes only those queued events. `praxile index update --path <asset>` marks a single asset dirty. `praxile index rebuild` remains the explicit deep repair path: it compares mtime/size first, reads files only when suspicious, then computes content hashes and UPSERTs changed assets.

`task_analysis.llm_assisted=false` keeps classification deterministic and free. Set it to `true` to call the configured model route for lightweight intent recognition. Use `cheap_model` for local/Ollama-style classification or `planning_model` for a stronger cloud route. LLM analysis can increase risk or add UI/privacy signals, but accepted frozen boundaries remain hard gates.

`model_roles` is the preferred model routing layer. Use `coding_agent` for strong code/action calls, `evidence_extraction` and `experience_reflection` for low-cost local learning work, `reward_judge` for optional quality judging, `proposal_composer` for reviewable proposal text, `review_recommendation` for cheap classification, `cheap_reasoner` as an optional local fallback, `feedback_classifier` for natural-language feedback routing, `attribution_judge` for asset usage attribution, `counterexample_checker` for pattern validation, `pattern_mining` for cross-run candidate discovery, `project_pattern_composer` for high-quality pattern cards, and `embedding` for retrieval vectors. Each non-embedding role can declare `fallback` entries. Legacy `routing.*_model` keys are still accepted and are useful for older configs and CLI overrides.

`semantic_judges.enabled=false` keeps self-evolution purely heuristic. Turn it on to add local cheap-model semantic judgement after heuristic candidate recall. Feedback classification is only used for complex feedback by default, attribution only judges relevant loaded assets, pattern mining only judges pairs above `only_after_heuristic_score`, and counterexample checking only validates likely conflicts. All outputs must be structured JSON; unavailable judges fall back to deterministic rules.

`evolution.llm_assisted_proposals=false` keeps experience proposal generation deterministic. Set it to `true` to let `model_roles.proposal_composer` or the legacy `evolution_model` route propose additional memories, skills, evals, failure patterns, harness rules, or routing notes. Those proposals must cite evidence, include confidence plus scope/anti-scope, target only safe `.praxile/` asset paths, and remain pending until accepted.

`memory.shard_enabled=true` keeps routine task memories in `memory/project.md` until the soft limit is reached; after that, new task memory proposals target date-based shards under `memory/shards/`. Consolidation can later merge and refine those shards without bloating the hot project memory file.

`retrieval.vector_enabled` and `retrieval.hybrid_enabled` enable the local SQLite vector table and combine vector scores with FTS results. `retrieval.vector_provider="local_hash"` is a lightweight lexical-vector fallback, not a strong semantic embedding model. Install `praxile[vector]` and set `retrieval.vector_provider="sentence_transformers"` for semantic embeddings. The priority, hybrid rank, and usage feedback weights are explicit config knobs so retrieval tuning is reviewable instead of hidden in code.

`browser.enabled` enables the optional Playwright adapter. Install `praxile[browser]`, run `python -m playwright install chromium`, and keep `allowed_hosts` narrow. Screenshots are stored under `.praxile/experience/artifacts/browser/` and serve as review evidence; human UX acceptance remains required for visual salience and interaction feel.

`architecture_gate.shadow_mode=true` lets Praxile record a dry-run shadow plan after a gate is triggered, but it still does not land file edits or implementation commands. The normal default is a hard stop.

`interop_guardrails` protects repositories used by multiple agent runtimes. If one of the configured lock files or environment flags is present, Praxile refuses normal project writes until the signal is removed or the guard is explicitly disabled.

`shell.allow_shell_features=false` keeps pipes, redirects, variable expansion, and command chaining blocked in the normal safe mode. Setting it to `true` allows shell execution only when each executable segment matches a reviewed prefix in `safety.allowed_command_prefixes`; dangerous command patterns and command substitution remain blocked, and redirection targets must stay inside the project.

`safety.backup_max_files` and `safety.backup_max_bytes` cap `.praxile/backups/`. Older backup files are removed first after new edit backups are written.

`reward.mode` supports `objective_only`, `objective_plus_user`, and `hybrid`. Hybrid reward combines objective execution signals, explicit user feedback, and optional `reward.llm_judge`. Missing optional components are not allowed to drag down the score; `final_reward.effective_weights` shows which active sources were used.

`reward.llm_judge` remains optional, but when enabled the expected structured output includes specificity, scope fit, evidence fit, intent alignment, overgeneralization risk, recommended action, and reasons. Praxile treats this as review assistance: high overgeneralization risk lowers proposal confidence and shifts guidance toward inspect or reject/edit, while objective tests and safety signals remain authoritative.

Use feedback commands to feed human judgement back into the loop:

```bash
praxile feedback latest --positive "干得好"
praxile feedback latest --negative "这次方向错了"
praxile feedback prop_123 --negative "这个 proposal 太泛了"
praxile feedback asset .praxile/skills/test-repair/SKILL.md --helpful
praxile feedback asset .praxile/rules/harness-rules/ui.md --harmful "这条规则误导了你"
praxile feedback auto "这次修得很好，但第二条 proposal 太泛，parser skill 先别再用了"
```

`feedback auto` is conservative natural-language routing. It can split one sentence into multiple feedback events targeting the latest run, a proposal such as `proposal:2`, a durable asset, or an accepted project pattern. Negative durable-asset feedback does not silently rewrite memory, skills, or rules; it records the signal and creates a reviewable governance proposal.

Run feedback updates `user_feedback_reward` and `final_reward`. Proposal feedback can lower confidence and change recommendation to `reject_or_edit`. Negative asset feedback increments negative outcome counts and creates a proposal-only lifecycle review rather than silently rewriting durable assets.

`reward.weights`, `reward.scores`, `reward.cost_thresholds`, `reward.scope.broad_edit_top_level_threshold`, and `reward.min_experience_value_for_proposals` make reward policy project-tunable. Empty verification is reported as `no_tests_available` when no tests were detected, or `detected_not_run` when Praxile detected commands but they were not executed. Reward reports now separate task execution from learning value with `objective_reward`, `user_feedback_reward`, `llm_judge_reward`, `final_reward`, `execution_score`, `safety_score`, `regression_score`, `scope_control_score`, `experience_value_score`, `proposal_quality_score`, and `should_generate_experience`.

`evolution.consolidation_min_duplicates`, `evolution.consolidation_stale_days`, and `evolution.consolidation_low_value_max_confidence` tune `praxile consolidate --all`. Consolidation remains proposal-only: it can propose `asset_merge`, `asset_deprecate`, `asset_rewrite`, and `asset_archive` governance updates plus cleanup review notes for duplicate, stale, conflicting, or low-value assets, but never deletes experience assets automatically.

`evolution.rejection_suppression_threshold=2` means repeated rejected proposals with the same type and similar concrete trigger terms suppress matching low-confidence future proposals. This keeps user rejection feedback from becoming a forgotten audit note.

## Model Transport

Provider calls use a transport abstraction:

```json
{
  "model": {
    "transport": "auto",
    "timeout_seconds": 30,
    "max_retries": 1,
    "retry_backoff_seconds": 0.25,
    "streaming": false
  }
}
```

Without optional dependencies, Praxile uses the built-in `urllib` transport. Install `praxile[http]` to allow `httpx` when `transport` is `auto` or `httpx`. Both transports expose `stream_json` for SSE-style model/event streams.

## Channel Bindings

Praxile's channel shape is inspired by OpenClaw-style gateway settings, but it is owned by Praxile and scoped to the current code project.

Bind Telegram:

```bash
praxile channel bind telegram -1001234567890 \
  --name team-alerts \
  --mode bidirectional \
  --token-env TELEGRAM_BOT_TOKEN \
  --free-response
```

Bind Discord:

```bash
praxile channel bind discord 123456789012345678 \
  --guild-id 987654321098765432 \
  --name dev-room \
  --mode task \
  --token-env DISCORD_BOT_TOKEN \
  --auto-thread
```

Inspect bindings:

```bash
praxile channel list
praxile channel show telegram:-1001234567890
praxile channel env
```

The generated config uses this structure:

```json
{
  "gateway": {
    "enabled": false,
    "host": "127.0.0.1",
    "port": 8765,
    "max_threads": 16,
    "token_env": "PRAXILE_GATEWAY_TOKEN",
    "channels_enabled": true
  },
  "channels": {
    "version": 1,
    "default": "telegram:-1001234567890",
    "bindings": {
      "telegram:-1001234567890": {
        "id": "telegram:-1001234567890",
        "platform": "telegram",
        "channel_id": "-1001234567890",
        "name": "team-alerts",
        "kind": "home",
        "enabled": true,
        "mode": "bidirectional",
        "token_env": "TELEGRAM_BOT_TOKEN",
        "require_mention": true,
        "allow_free_response": true,
        "skill": "bugfix-review",
        "prompt": "Treat messages in this chat as project-scoped Praxile tasks."
      }
    },
    "platforms": {
      "telegram": {
        "enabled": true,
        "token_env": "TELEGRAM_BOT_TOKEN",
        "home_channel": "telegram:-1001234567890",
        "require_mention": true,
        "free_response_chats": ["-1001234567890"]
      },
      "discord": {
        "enabled": false,
        "token_env": "DISCORD_BOT_TOKEN",
        "home_channel": null,
        "require_mention": true,
        "free_response_channels": [],
        "allowed_channels": [],
        "auto_thread": true,
        "channel_skill_bindings": {},
        "channel_prompts": {}
      }
    }
  }
}
```

## Current Boundary

The current release implements channel configuration, binding management, gateway introspection, and local route metadata. It does not yet run production Telegram/Discord bot listeners. That listener layer can be added on top of the same config without changing `.praxile/` state semantics.

The built-in local HTTP gateway uses a bounded worker pool. Tune `gateway.max_threads` when running local API clients with higher concurrency.
