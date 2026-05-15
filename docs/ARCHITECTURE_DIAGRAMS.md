# Praxile Architecture Diagrams

This document contains the redesigned architecture diagrams used by the README.

---

## 1. Architecture at a glance

```mermaid
flowchart LR
    classDef input fill:#EEF4FF,stroke:#5B8DEF,color:#16325C,stroke-width:1.5px;
    classDef interface fill:#F6F8FA,stroke:#7B8794,color:#1F2937,stroke-width:1.5px;
    classDef runtime fill:#F3F7FF,stroke:#4F7FD9,color:#102A56,stroke-width:1.5px;
    classDef engine fill:#F7F0FF,stroke:#8B5CF6,color:#352063,stroke-width:1.5px;
    classDef gov fill:#FFF7E8,stroke:#D8942A,color:#4A3200,stroke-width:1.5px;
    classDef asset fill:#EFFAF0,stroke:#45A66A,color:#12351F,stroke-width:1.5px;
    classDef audit fill:#F2F4F7,stroke:#667085,color:#182230,stroke-width:1.5px;

    U["User Task<br/>feedback"]:::input
    S["Spec Context<br/>spec.md · plan.md · tasks.md · constitution.md"]:::input

    U --> I["Interfaces<br/>CLI · Terminal · Gateway"]:::interface
    S --> I

    I --> R["Runtime Harness<br/>task analyzer · model router · tools · tests · safety · workspace"]:::runtime
    R --> T["Trajectory Ledger<br/>actions · observations · diffs · commands · artifacts"]:::runtime
    T --> E["Experience Engine<br/>reward · evidence · episodes · patterns"]:::engine
    E --> G["Governance Layer<br/>silent-failure signals · proposal gate · human review"]:::gov
    G --> A["Repository Assets<br/>memory · skill · rule · eval · pattern · boundary"]:::asset
    A --> Q["Future Retrieval<br/>explain · attribution · consolidation"]:::asset
    Q --> R

    G --> O["Audit & Provenance<br/>graph · redaction · CI gates · rollback"]:::audit
    A --> O
    T --> O
```

---

## 2. Core governed experience loop

```mermaid
flowchart LR
    classDef step fill:#F6F8FA,stroke:#7B8794,color:#1F2937,stroke-width:1.3px;
    classDef gate fill:#FFF7E8,stroke:#D8942A,color:#4A3200,stroke-width:1.3px;
    classDef asset fill:#EFFAF0,stroke:#45A66A,color:#12351F,stroke-width:1.3px;
    classDef weak fill:#FFF1F3,stroke:#E31B54,color:#7A271A,stroke-width:1.3px;

    A["Run"]:::step --> B["Trajectory"]:::step --> C["Reward Report"]:::step --> D["Evidence"]:::step --> E["Episode"]:::step --> F["Pattern"]:::step --> G["Proposal"]:::step
    G --> H["Proposal Gate"]:::gate
    H -->|pass| I["Human Review"]:::gate
    H -->|suppress| W["Weak Candidate<br/>run summary only"]:::weak
    I -->|accept| J["Active Asset"]:::asset
    I -->|edit / reject| K["Review Signal"]:::gate
    J --> L["Future Retrieval"]:::asset
    K --> M["Feedback / Counterexample"]:::step
    L --> A
    M --> F
```

---

## 3. Experience graph / provenance graph

```mermaid
flowchart TB
    classDef spec fill:#EEF4FF,stroke:#5B8DEF,color:#16325C,stroke-width:1.3px;
    classDef run fill:#F6F8FA,stroke:#7B8794,color:#1F2937,stroke-width:1.3px;
    classDef proposal fill:#FFF7E8,stroke:#D8942A,color:#4A3200,stroke-width:1.3px;
    classDef asset fill:#EFFAF0,stroke:#45A66A,color:#12351F,stroke-width:1.3px;
    classDef feedback fill:#F7F0FF,stroke:#8B5CF6,color:#352063,stroke-width:1.3px;

    S["Spec / Constitution"]:::spec -->|derived_from_spec| R1["Run"]:::run
    R1 -->|produced| E["Evidence / Episode"]:::run
    E -->|supports_proposal| P["Proposal"]:::proposal
    R1 -->|generated_from_run| P
    P -->|approved_by| A["Asset"]:::asset
    A -->|retrieved_in_run| R2["Future Run"]:::run
    A -->|helped_run| R2
    A -->|misled_run| R3["Failed / Risky Run"]:::run
    F["Feedback"]:::feedback -->|adjusts_confidence| A
    A -->|supersedes| A2["Older Asset"]:::asset
    P -->|rejected_as| X["Rejected Signal"]:::proposal
```

---

## 4. Layer model

```mermaid
flowchart TB
    L1["Input Layer<br/>User Task · Spec · Feedback"]
    L2["Interface Layer<br/>CLI · Terminal · Gateway (Web Console)"]
    L3["Runtime Harness<br/>Task Analyzer · Model Router · Tools · Tests · Safety · Workspace"]
    L4["Trajectory Ledger<br/>Actions · Observations · Diffs · Commands · Artifacts"]
    L5["Experience Engine<br/>Reward · Evidence · Episodes · Patterns"]
    L6["Governance Layer<br/>Silent Failure · Proposal Gate · Human Review"]
    L7["Repository Assets<br/>Memory · Skills · Rules · Evals · Patterns · Boundaries"]
    L8["Provenance & Audit<br/>Graph · Redaction · CI Gate · Rollback"]
    L9["Retrieval Layer<br/>FTS · Vector · Attribution · Consolidation · Reflect"]

    L1 --> L2 --> L3 --> L4 --> L5 --> L6 --> L7 --> L9
    L4 --> L8
    L6 --> L8
    L7 --> L8
    L9 --> L3
```
