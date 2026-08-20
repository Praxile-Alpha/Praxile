# Agent Harness Landscape 2026

> Research snapshot: 2026-08-12. Product capabilities and preprints can change quickly. Links below point to official product documentation, official repositories, protocol specifications, or paper pages.

## Executive Summary

Agent harnesses have moved beyond a model-plus-tools loop. The production baseline now includes context management, durable execution, permissions, checkpoints, human approval, observability, memory, skills, model routing, and protocol interoperability.

The research frontier has moved one step further: using trajectories and evaluation evidence to improve the harness itself. The central problem is no longer generating a memory or prompt patch. It is determining whether a proposed change caused a generalizable improvement without weakening safety or overfitting its source tasks.

```text
Model capability
      +
Harness quality: context · tools · runtime · memory · policy · evaluation
      =
Observed agent performance
```

## Industrial Landscape

| Category | Representative systems | Direction |
|---|---|---|
| General work agents | ChatGPT Work, Tencent WorkBuddy, OpenClaw-style systems | Files, apps, browser, desktop execution, remote supervision, deliverable creation |
| Coding agents | Codex, Claude Code, CodeBuddy, OpenHands | Repository understanding, terminal, tests, browser verification, parallel work, PR workflows |
| Durable runtimes | LangGraph, Microsoft Agent Framework, Google ADK | Checkpoints, resumability, typed workflows, human interrupts, multi-agent orchestration |
| Interoperability | MCP, A2A, ACP, AG-UI/A2UI | Standard connections among tools, agents, editors, and interactive clients |
| Harness evolution | MemoHarness, Self-Harness, HASE, GSME, HarnessCompass | Learn from trajectories, diagnose failure modes, propose and validate harness changes |

### ChatGPT Work and Codex

OpenAI's current product direction separates conversational exploration, knowledge work, and engineering execution. ChatGPT Work is described as an agent that can act across apps and files and remain with a project for extended work. Codex spans local and remote environments, exposes review and approval surfaces, and supports skills, plugins, hooks, remote supervision, and long-running work.

This is important for Praxile because the product baseline is shifting from "can the agent call a tool?" to "can the user supervise a durable, inspectable task across environments?"

Sources:

- [OpenAI Developers: ChatGPT Work, Codex, plugins, and current workflows](https://developers.openai.com/)
- [Codex use cases](https://developers.openai.com/codex/use-cases)
- [OpenAI model and agent guidance](https://developers.openai.com/api/docs/guides/latest-model)

### Tencent WorkBuddy and CodeBuddy

WorkBuddy is positioned as a desktop work agent that can operate on authorized local files and terminal workflows, create office artifacts, analyze data, and coordinate broader work. CodeBuddy covers development through IDE, plugin, and CLI surfaces. Their product strength is broad execution coverage and Tencent ecosystem integration.

Public documentation currently emphasizes task completion, multimodal deliverables, and ecosystem connectivity. It does not establish a proposal-governed trajectory-to-experience evolution model equivalent to Praxile's intended boundary.

Sources:

- [WorkBuddy quick start](https://www.codebuddy.ai/docs/zh/workbuddy/Quickstart)
- [WorkBuddy product guide](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Product-Guide)

### OpenHands and durable workflow frameworks

OpenHands exposes a composable software-agent SDK with local or ephemeral workspaces, tools, skills, an Agent Server, and REST APIs. LangGraph treats persistence as a foundation for human approval, memory, time-travel debugging, and fault recovery. These systems show that checkpointing and explicit execution state are becoming production requirements.

Sources:

- [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangChain human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)
- [A2A protocol specification](https://a2a-protocol.org/v0.3.0/specification/)

## Research Frontier

### From self-editing to evidence-gated evolution

Early work such as [A Self-Improving Coding Agent](https://arxiv.org/abs/2504.15228) demonstrated that an agent can edit its own implementation and improve benchmark performance. Recent work focuses on safer attribution and generalization:

- [Self-Harness](https://arxiv.org/abs/2606.09498) uses weakness mining, minimal harness proposals, and regression validation.
- [MemoHarness](https://arxiv.org/abs/2607.14159) stores case diagnoses and distilled global patterns, then adapts harness controls using retrieved experience.
- [Harness-Aware Self-Evolving](https://arxiv.org/abs/2607.03935) co-evolves selected harness components, task solutions, and model behavior.
- [Gated Semantic Quality-Diversity](https://arxiv.org/abs/2607.13683) separates LLM proposal generation from deterministic measurement and significance testing.
- [HarnessCompass](https://arxiv.org/abs/2608.01918) constrains evolution, collects proactive usage feedback, and optimizes harness components separately before consolidation.
- [Hierarchical Self-Improvement](https://arxiv.org/abs/2608.08466) distinguishes task harness, evolver, and meta-evolver while retaining a frozen outer anchor.

### Stored experience is not effective experience

[Harness Updating Is Not Harness Benefit](https://arxiv.org/abs/2605.30621) separates two capabilities:

1. producing useful harness updates;
2. activating and benefiting from those updates during later tasks.

This distinction matters directly to Praxile. Indexing a `SKILL.md` or memory and retrieving it later proves storage and retrieval, not behavioral adoption. A credible system must measure the full activation funnel and connect it to outcomes.

### Harness choice can rival model choice

[Claw-SWE-Bench](https://arxiv.org/abs/2606.12344) evaluates heterogeneous general-purpose harnesses under a common coding contract. Its reported sweeps show large performance variation from both model choice and harness choice, while also exposing substantial cost differences. The practical implication is that provider selection and harness configuration must be evaluated together.

### LLM judges are useful but not ground truth

[REFLECT](https://arxiv.org/abs/2605.19196) evaluates LLM judges using controlled interventions in research-agent trajectories and reports that current judges remain unreliable across reasoning, tool-use, and evidence failures. Semantic judges can enrich evaluation, but they require calibration, evidence access, disagreement handling, and human escalation.

## Implications for Praxile

Praxile already has the right substrate: repository-local trajectories, reward reports, evidence, proposals, approval, lifecycle state, retrieval attribution, Reflect, rollback, and an experience graph. The next differentiation should come from proving improvement rather than adding more asset formats.

The required progression is:

```text
Current governed experience
  -> observable experience activation
  -> shadow validation and sealed regression cases
  -> component-level harness experiments
  -> calibrated reward and judge governance
  -> bounded, versioned harness evolution
```

Praxile should retain three product distinctions:

- project-scoped experience instead of silent global memory;
- reviewable, reversible updates instead of unrestricted self-modification;
- evidence-backed improvement claims instead of treating LLM summaries as learning.

The engineering translation is maintained in [Trusted Harness Evolution Roadmap](TRUSTED_HARNESS_EVOLUTION_ROADMAP.md).
