from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape

from .config import Config


@dataclass(frozen=True)
class ConsolePage:
    html: str


def render_console(config: Config) -> ConsolePage:
    project = escape(str(config.paths.root))
    state = escape(str(config.paths.state))
    project_json = json.dumps(str(config.paths.root))
    html = _HTML.replace("__PROJECT__", project).replace("__STATE__", state).replace("__PROJECT_JSON__", project_json)
    return ConsolePage(html)


_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Praxile Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f6f7;
      --panel: #ffffff;
      --text: #17191c;
      --muted: #626b75;
      --line: #d9dee5;
      --soft: #eef1f4;
      --accent: #0f766e;
      --accent-weak: #e0f2f1;
      --danger: #b42318;
      --warn: #9a6700;
      --ok: #176b3a;
      --code: #202327;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    button, input, textarea, select { font: inherit; }
    button {
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      min-height: 34px;
      padding: 7px 10px;
      cursor: pointer;
    }
    button.secondary { background: #fff; color: var(--accent); }
    button.ghost { border-color: transparent; background: transparent; color: var(--text); }
    button.danger { border-color: var(--danger); background: var(--danger); }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 8px 9px;
    }
    textarea { resize: vertical; min-height: 72px; }
    .shell {
      height: 100vh;
      display: grid;
      grid-template-columns: 260px minmax(420px, 1fr) 330px;
      min-width: 0;
    }
    aside, main {
      min-width: 0;
      border-right: 1px solid var(--line);
      background: var(--panel);
    }
    .left, .right {
      overflow: auto;
      padding: 14px;
    }
    .brand { font-weight: 700; font-size: 18px; margin-bottom: 3px; }
    .meta {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    nav {
      display: grid;
      gap: 5px;
      margin: 16px 0;
    }
    nav button {
      width: 100%;
      justify-content: flex-start;
      text-align: left;
      border-color: transparent;
      background: transparent;
      color: var(--text);
    }
    nav button.active { background: var(--accent-weak); border-color: var(--accent-weak); color: #0b4f4a; }
    h1, h2, h3 { margin: 0; }
    h1 { font-size: 18px; }
    h2 { font-size: 14px; margin-bottom: 9px; }
    h3 { font-size: 13px; margin: 14px 0 8px; }
    .section {
      border-top: 1px solid var(--line);
      padding-top: 12px;
      margin-top: 12px;
    }
    .list { display: grid; gap: 7px; }
    .item {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 9px;
      min-width: 0;
    }
    .item strong {
      display: block;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .item span, .subtle {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .main {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      background: #fbfbfc;
    }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }
    .content { overflow: auto; padding: 16px; }
    .messages { display: grid; gap: 10px; max-width: 920px; margin: 0 auto; }
    .message {
      max-width: 78%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      background: #fff;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .message.user {
      justify-self: end;
      border-color: #b6d8d5;
      background: #eef8f7;
    }
    .message.assistant { justify-self: start; }
    .message-card {
      margin-top: 8px;
      border-top: 1px solid var(--line);
      padding-top: 8px;
      display: grid;
      gap: 7px;
    }
    .message-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .tool-calls {
      display: grid;
      gap: 5px;
    }
    .tool-call {
      display: grid;
      grid-template-columns: minmax(90px, 1fr) 82px 70px;
      gap: 6px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 5px 7px;
      background: #fafbfc;
      font-size: 12px;
    }
    .progress {
      max-width: 920px;
      margin: 0 auto 12px;
      display: grid;
      gap: 6px;
    }
    .event {
      display: grid;
      grid-template-columns: 90px minmax(0, 1fr) 70px;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 7px 9px;
      font-size: 12px;
      align-items: center;
    }
    .artifact-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 10px 0;
    }
    .artifact-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 9px;
      min-width: 0;
    }
    .artifact-card strong { display: block; font-size: 13px; margin-bottom: 5px; }
    .diff-block {
      background: #15181c;
      color: #f5f7fa;
      border-radius: 8px;
      padding: 10px;
      max-height: 360px;
      overflow: auto;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      white-space: pre-wrap;
    }
    .composer {
      border-top: 1px solid var(--line);
      background: #fff;
      padding: 12px 16px;
    }
    .composer-inner {
      max-width: 920px;
      margin: 0 auto;
      display: grid;
      gap: 9px;
    }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .grid3 { display: grid; grid-template-columns: 1fr 1fr 120px; gap: 8px; }
    .toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .mini-form {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 10px;
      display: grid;
      gap: 8px;
      margin-bottom: 10px;
    }
    .check { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 13px; }
    .check input { width: auto; }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      border: 1px solid var(--line);
      padding: 2px 7px;
      font-size: 12px;
      color: var(--muted);
      background: #fff;
    }
    .badge.ok { color: var(--ok); border-color: #b7dfc5; background: #effaf3; }
    .badge.warn { color: var(--warn); border-color: #ead49a; background: #fff8e6; }
    .badge.danger { color: var(--danger); border-color: #efc1bd; background: #fff2f1; }
    .panel { display: none; }
    .panel.active { display: block; }
    table {
      width: 100%;
      border-collapse: collapse;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 8px;
      text-align: left;
      vertical-align: top;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    th { color: var(--muted); font-weight: 600; background: #f8fafb; }
    tr:last-child td { border-bottom: 0; }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: var(--code);
      color: #f7f7f7;
      border-radius: 8px;
      padding: 11px;
      max-height: 460px;
      overflow: auto;
      font-size: 12px;
    }
    .right { border-right: 0; }
    .kv { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 6px; font-size: 13px; }
    .kv div:nth-child(odd) { color: var(--muted); }
    @media (max-width: 1080px) {
      .shell { grid-template-columns: 220px minmax(0, 1fr); }
      .right { display: none; }
    }
    @media (max-width: 760px) {
      .shell { height: auto; grid-template-columns: 1fr; }
      aside, main { border-right: 0; border-bottom: 1px solid var(--line); }
      .message { max-width: 100%; }
      .grid2, .grid3 { grid-template-columns: 1fr; }
      .artifact-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="left">
      <div class="brand">Praxile</div>
      <div class="meta">__PROJECT__</div>
      <nav>
        <button class="active" data-panel="chat">Chat</button>
        <button data-panel="models">Model Roles</button>
        <button data-panel="runs">Runs</button>
        <button data-panel="proposals">Proposals</button>
        <button data-panel="assets">Assets</button>
        <button data-panel="reflect">Reflect</button>
        <button data-panel="graph">Graph</button>
        <button data-panel="audit">Audit</button>
        <button data-panel="spec">Spec</button>
        <button data-panel="safety">Safety</button>
        <button data-panel="channels">Channels</button>
        <button data-panel="ci">CI / PR</button>
        <button data-panel="repos">Repos</button>
      </nav>
      <div class="section">
        <h2>Project</h2>
        <div class="kv" id="project-kv"></div>
      </div>
      <div class="section">
        <h2>Recent Runs</h2>
        <div class="list" id="left-runs"></div>
      </div>
      <div class="section">
        <h2>Pending Proposals</h2>
        <div class="list" id="left-proposals"></div>
      </div>
    </aside>

    <main class="main">
      <div class="topbar">
        <div>
          <h1 id="panel-title">Chat Workspace</h1>
          <div class="subtle">__STATE__</div>
        </div>
        <div class="toolbar">
          <button class="secondary" id="refresh">Refresh</button>
          <button class="secondary" id="new-session">New Session</button>
        </div>
      </div>

      <div class="content">
        <section id="panel-chat" class="panel active">
          <div class="progress" id="run-progress"></div>
          <div class="messages" id="messages"></div>
        </section>

        <section id="panel-models" class="panel">
          <h2>Model Roles</h2>
          <div class="toolbar" style="margin-bottom:10px">
            <button class="secondary" id="test-models">Test all routes</button>
          </div>
          <div id="models-table"></div>
          <h3>Edit Role Route</h3>
          <div class="mini-form">
            <div class="grid3">
              <select id="role-edit-name"></select>
              <input id="role-edit-provider" placeholder="provider id, e.g. openai">
              <input id="role-edit-model" placeholder="model, e.g. gpt-4.1">
            </div>
            <div class="grid3">
              <select id="role-edit-mode">
                <option value="required">required</option>
                <option value="recommended">recommended</option>
                <option value="optional">optional</option>
                <option value="disabled">disabled</option>
              </select>
              <input id="role-edit-fallback" placeholder="fallback targets, comma-separated">
              <select id="role-edit-enabled">
                <option value="true">enabled</option>
                <option value="false">disabled</option>
              </select>
            </div>
            <div class="toolbar">
              <button class="secondary" id="save-role-route">Save role route</button>
              <button class="secondary" id="test-role-route">Test selected role</button>
            </div>
          </div>
          <h3>Providers</h3>
          <div id="providers-table"></div>
          <h3>Add / Update Provider</h3>
          <div class="mini-form">
            <div class="grid3">
              <input id="provider-edit-id" placeholder="provider id">
              <select id="provider-edit-type">
                <option value="openai_compatible">OpenAI-compatible</option>
                <option value="anthropic">Anthropic</option>
                <option value="ollama">Ollama</option>
                <option value="local">Local</option>
                <option value="custom">Custom</option>
              </select>
              <input id="provider-edit-timeout" type="number" min="1" placeholder="timeout seconds">
            </div>
            <div class="grid3">
              <input id="provider-edit-base-url" placeholder="base URL">
              <input id="provider-edit-key-env" placeholder="API key env, never raw key">
              <input id="provider-edit-models" placeholder="models, comma-separated">
            </div>
            <div class="toolbar">
              <button class="secondary" id="save-provider">Save provider</button>
            </div>
          </div>
          <h3>Route Stats</h3>
          <div id="model-stats"></div>
        </section>

        <section id="panel-runs" class="panel">
          <h2>Runs</h2>
          <div class="toolbar" style="margin-bottom:10px">
            <button class="secondary" id="load-jobs">Background jobs</button>
          </div>
          <div id="runs-table"></div>
          <h3>Readable Artifacts</h3>
          <div id="run-artifacts"></div>
          <h3>Run Detail</h3>
          <pre id="run-detail">Select a run.</pre>
        </section>

        <section id="panel-proposals" class="panel">
          <h2>Proposal Inbox</h2>
          <div id="proposals-table"></div>
          <h3>Proposal Detail</h3>
          <pre id="proposal-detail">Select a proposal.</pre>
          <h3>Edit Pending Proposal</h3>
          <textarea id="proposal-editor" style="min-height:260px;font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" placeholder="Open a pending proposal to edit its JSON."></textarea>
          <div class="toolbar" style="margin-top:8px">
            <button class="secondary" id="save-proposal-edit">Save Edit</button>
          </div>
        </section>

        <section id="panel-assets" class="panel">
          <h2>Assets</h2>
          <div id="assets-table"></div>
          <h3>Asset Detail</h3>
          <pre id="asset-detail">Select an asset.</pre>
          <div class="toolbar" style="margin-top:8px">
            <button class="secondary" id="asset-usage">Usage</button>
            <button class="secondary" id="asset-graph">Graph</button>
            <button class="secondary" id="asset-deprecate">Deprecate</button>
            <button class="secondary" id="asset-archive">Archive</button>
            <button class="secondary" id="asset-reactivate">Reactivate</button>
          </div>
        </section>

        <section id="panel-reflect" class="panel">
          <h2>Reflect Dashboard</h2>
          <div class="grid3" style="margin-bottom:10px">
            <input id="reflect-since" placeholder="since, e.g. 7d">
            <input id="reflect-asset" placeholder=".praxile asset path">
            <input id="reflect-stale-days" type="number" min="1" value="30">
          </div>
          <div class="toolbar" style="margin-bottom:10px">
            <label class="check"><input id="reflect-duplicates" type="checkbox" checked> duplicates</label>
            <label class="check"><input id="reflect-stale" type="checkbox" checked> stale</label>
            <label class="check"><input id="reflect-silent" type="checkbox" checked> silent failures</label>
            <label class="check"><input id="reflect-harmful" type="checkbox"> harmful</label>
            <label class="check"><input id="reflect-write" type="checkbox"> write proposals</label>
            <button id="run-reflect">Run Reflect</button>
            <button class="secondary" id="load-reflect">Load Reports</button>
          </div>
          <div id="reflect-table"></div>
          <h3>Reflect Output</h3>
          <pre id="reflect-detail">No reflect report loaded.</pre>
        </section>

        <section id="panel-graph" class="panel">
          <h2>Graph Explorer</h2>
          <div class="grid3" style="margin-bottom:10px">
            <input id="graph-ref" placeholder="run id, proposal id, or asset path">
            <input id="graph-depth" type="number" min="1" max="5" value="2">
            <input id="graph-limit" type="number" min="1" max="500" value="100">
          </div>
          <div class="toolbar" style="margin-bottom:10px">
            <button id="explain-graph">Explain</button>
            <button class="secondary" id="graph-status">Status</button>
            <button class="secondary" id="rebuild-graph">Rebuild</button>
          </div>
          <pre id="graph-detail">No graph query loaded.</pre>
        </section>

        <section id="panel-audit" class="panel">
          <h2>Audit Dashboard</h2>
          <div class="grid3" style="margin-bottom:10px">
            <input id="audit-limit-runs" type="number" min="1" max="200" value="20">
            <select id="audit-redaction">
              <option value="standard">standard</option>
              <option value="strict">strict</option>
              <option value="none">none</option>
            </select>
            <select id="audit-strict">
              <option value="false">normal</option>
              <option value="true">strict</option>
            </select>
          </div>
          <div class="toolbar" style="margin-bottom:10px">
            <label class="check"><input id="audit-rebuild-graph" type="checkbox"> rebuild graph</label>
            <label class="check"><input id="audit-include-reflect" type="checkbox" checked> include reflect</label>
            <button id="audit-check">Check</button>
            <button class="secondary" id="audit-bundle">Bundle</button>
          </div>
          <pre id="audit-detail">No audit report loaded.</pre>
        </section>

        <section id="panel-spec" class="panel">
          <h2>Spec Verify</h2>
          <div class="grid3" style="margin-bottom:10px">
            <input id="spec-path" placeholder="spec.md or docs/specs/name.md">
            <input id="spec-run" placeholder="run id, or latest" value="latest">
            <input id="spec-extra" placeholder="additional specs, comma-separated">
          </div>
          <div class="toolbar" style="margin-bottom:10px">
            <button id="spec-list">List</button>
            <button class="secondary" id="spec-check">Check</button>
            <button class="secondary" id="spec-verify">Verify</button>
          </div>
          <pre id="spec-detail">No spec report loaded.</pre>
        </section>

        <section id="panel-safety" class="panel">
          <h2>Tool / Safety Policy</h2>
          <div class="toolbar" style="margin-bottom:10px">
            <button id="load-tools">Load Tools</button>
            <button class="secondary" id="load-safety">Load Policy</button>
          </div>
          <div id="tools-table"></div>
          <h3>Check Command</h3>
          <div class="grid2" style="margin-bottom:8px">
            <input id="safety-command" placeholder="python -m pytest">
            <button class="secondary" id="check-command">Check command</button>
          </div>
          <h3>Check Path</h3>
          <div class="grid3" style="margin-bottom:8px">
            <input id="safety-path" placeholder="src/app.py">
            <select id="safety-path-write">
              <option value="false">read</option>
              <option value="true">write</option>
            </select>
            <button class="secondary" id="check-path">Check path</button>
          </div>
          <h3>Safety Output</h3>
          <pre id="safety-detail">No safety policy loaded.</pre>
        </section>

        <section id="panel-channels" class="panel">
          <h2>Channel Bindings</h2>
          <div id="channels-table"></div>
          <h3>Bind Telegram / Discord</h3>
          <div class="mini-form">
            <div class="grid3">
              <select id="channel-platform">
                <option value="telegram">telegram</option>
                <option value="discord">discord</option>
              </select>
              <input id="channel-id" placeholder="channel/chat id">
              <input id="channel-guild-id" placeholder="discord guild id">
            </div>
            <div class="grid3">
              <input id="channel-name" placeholder="display name">
              <select id="channel-kind">
                <option value="home">home</option>
                <option value="project">project</option>
                <option value="alert">alert</option>
                <option value="review">review</option>
              </select>
              <select id="channel-mode">
                <option value="notify">notify</option>
                <option value="task">task</option>
                <option value="bidirectional">bidirectional</option>
              </select>
            </div>
            <div class="grid3">
              <input id="channel-token-env" placeholder="token env, e.g. TELEGRAM_BOT_TOKEN">
              <input id="channel-skill" placeholder="optional skill">
              <input id="channel-prompt" placeholder="optional prompt">
            </div>
            <div class="toolbar">
              <label class="check"><input id="channel-default" type="checkbox"> default</label>
              <label class="check"><input id="channel-require-mention" type="checkbox" checked> require mention</label>
              <button class="secondary" id="bind-channel">Bind channel</button>
            </div>
          </div>
          <h3>Channel Output</h3>
          <pre id="channels-detail">No channel binding loaded.</pre>
        </section>

        <section id="panel-ci" class="panel">
          <h2>CI / PR Reports</h2>
          <div class="toolbar" style="margin-bottom:10px">
            <button class="secondary" id="load-ci">Load CI reports</button>
          </div>
          <div id="ci-table"></div>
          <h3>Report Detail</h3>
          <pre id="ci-detail">No CI or PR report loaded.</pre>
        </section>

        <section id="panel-repos" class="panel">
          <h2>Multi-repo Dashboard</h2>
          <div class="toolbar" style="margin-bottom:10px">
            <button class="secondary" id="load-repos">Load repos</button>
          </div>
          <div id="repos-table"></div>
          <h3>Repo Output</h3>
          <pre id="repos-detail">No repo status loaded.</pre>
        </section>
      </div>

      <div class="composer">
        <div class="composer-inner">
          <textarea id="task" placeholder="Fix the failing parser test"></textarea>
          <div class="grid3">
            <input id="spec" placeholder="spec path">
            <input id="tests" placeholder="test command">
            <input id="steps" type="number" min="1" max="50" value="10">
          </div>
          <div class="grid2">
            <select id="workspace">
              <option value="in-place">in-place</option>
              <option value="copy">copy</option>
              <option value="git-worktree">git-worktree</option>
            </select>
            <input id="model-override" placeholder="temporary coding_agent route, e.g. provider:model">
          </div>
          <div class="toolbar">
            <button id="run">Run</button>
            <button class="secondary" id="retry-last">Retry last</button>
            <button class="secondary" id="stop-session">Stop</button>
            <label class="check"><input id="dry-run" type="checkbox"> Dry-run</label>
            <label class="check"><input id="allow-shell" type="checkbox"> Allow shell features</label>
          </div>
        </div>
      </div>
    </main>

    <aside class="right">
      <h2>Governance Context</h2>
      <div class="list" id="governance"></div>
      <div class="section">
        <h2>Loaded Assets</h2>
        <div class="list" id="loaded-assets"></div>
      </div>
      <div class="section">
        <h2>Silent Risks</h2>
        <div class="list" id="silent-risks"></div>
      </div>
      <div class="section">
        <h2>Generated Proposals</h2>
        <div class="list" id="run-proposals"></div>
      </div>
    </aside>
  </div>

  <script>
    const projectRoot = __PROJECT_JSON__;
    let currentSession = null;
    let latestRun = null;
    let currentProposalId = null;
    let currentAssetPath = null;
    let cachedRoles = [];
    let cachedProviders = [];
    let activeJobId = null;
    let activeJobSeq = 0;
    let activeEventSource = null;
    let activePoll = null;
    const $ = id => document.getElementById(id);

    async function api(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: {'Content-Type': 'application/json', ...(options.headers || {})}
      });
      const data = await response.json();
      if (!data.ok) throw new Error(data.error || 'Request failed');
      return data.result;
    }
    function esc(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    function item(title, body, cls = '') {
      return `<div class="item ${cls}"><strong>${esc(title)}</strong><span>${esc(body)}</span></div>`;
    }
    function badge(value) {
      const text = String(value ?? 'unknown');
      const cls = ['completed','ok','accepted','configured','connected'].includes(text) ? 'ok' : (['failed','high','missing_key','provider_missing'].includes(text) ? 'danger' : 'warn');
      return `<span class="badge ${cls}">${esc(text)}</span>`;
    }
    function table(headers, rows) {
      if (!rows.length) return '<div class="item"><strong>No records</strong><span></span></div>';
      return `<table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
    }
    function setPanel(name) {
      document.querySelectorAll('nav button').forEach(btn => btn.classList.toggle('active', btn.dataset.panel === name));
      document.querySelectorAll('.panel').forEach(panel => panel.classList.toggle('active', panel.id === `panel-${name}`));
      $('panel-title').textContent = {chat:'Chat Workspace', models:'Model Roles', runs:'Runs', proposals:'Proposals', assets:'Assets', reflect:'Reflect Dashboard', graph:'Graph Explorer', audit:'Audit Dashboard', spec:'Spec Verify', safety:'Tool / Safety Policy', channels:'Channel Bindings', ci:'CI / PR Reports', repos:'Multi-repo Dashboard'}[name] || 'Praxile';
    }
    function renderMessages(session) {
      const messages = session?.messages || [];
      $('messages').innerHTML = messages.length ? messages.map(msg => {
        const summary = msg.governance_summary || {};
        const toolCalls = (msg.tool_calls || []).slice(0, 6);
        const governance = msg.run_id ? `
          <div class="message-card">
            <div class="toolbar">
              ${badge(msg.run_id)}
              ${badge(summary.reward ?? 'reward n/a')}
              ${badge(`${summary.proposals ?? 0} proposals`)}
              ${badge(`${summary.silent_failure_signals ?? 0} risks`)}
            </div>
            <div class="message-actions">
              <button class="secondary" onclick="openRun('${esc(msg.run_id)}')">Open run</button>
              <button class="secondary" onclick="setPanel('proposals')">Review proposals</button>
              <button class="secondary" onclick="setPanel('graph')">Open graph</button>
              <button class="secondary" onclick="setPanel('audit')">Open audit</button>
            </div>
            ${toolCalls.length ? `<div class="tool-calls">${toolCalls.map(call => `<div class="tool-call"><span>${esc(call.type || call.action_type || 'tool')}</span><span>${badge(call.status)}</span><span>${esc(call.risk_level || '')}</span></div>`).join('')}</div>` : ''}
          </div>` : '';
        return `<div class="message ${esc(msg.role)}">${esc(msg.content || '')}${governance}</div>`;
      }).join('') : '<div class="message assistant">Ready.</div>';
    }
    async function ensureSession() {
      if (currentSession) return currentSession;
      currentSession = await api('/api/chat/sessions', {method:'POST', body: JSON.stringify({title:'Web session'})});
      renderMessages(currentSession);
      return currentSession;
    }
    function renderGovernance(run) {
      latestRun = run || latestRun;
      const summary = latestRun?.governance_summary || {};
      $('governance').innerHTML = [
        item('Run', latestRun?.task_id || 'none'),
        item('Status', latestRun?.status || 'none'),
        item('Reward', summary.reward ?? latestRun?.reward ?? 'none'),
        item('Proposals', summary.proposals ?? 0),
        item('Silent risks', summary.silent_failure_signals ?? 0)
      ].join('');
      $('loaded-assets').innerHTML = (latestRun?.loaded_assets || []).slice(0, 8).map(asset => item(asset.path || asset.title || 'asset', asset.why_loaded || asset.type || '')).join('') || item('None', '');
      $('silent-risks').innerHTML = (latestRun?.silent_failure_signals || []).map(sig => item(sig.type || sig.signal || 'risk', sig.reason || sig.risk || '')).join('') || item('None', '');
      $('run-proposals').innerHTML = (latestRun?.proposals || []).map(prop => item(prop.proposal_id, `${prop.type} ${prop.risk_level || ''}`)).join('') || item('None', '');
    }
    function renderProgress(events, job = null) {
      const header = job ? item(`Job ${job.job_id}`, `${job.status} ${job.stop_requested ? '(stop requested)' : ''}`) : '';
      const rows = (events || []).slice(-12).map(event => `<div class="event"><span>${esc(event.stage || event.type)}</span><span>${esc(event.message || '')}</span><span>#${esc(event.seq || '')}</span></div>`).join('');
      $('run-progress').innerHTML = header + (rows || '');
    }
    function stopJobWatcher() {
      if (activeEventSource) {
        activeEventSource.close();
        activeEventSource = null;
      }
      if (activePoll) {
        clearInterval(activePoll);
        activePoll = null;
      }
    }
    function watchJob(jobId) {
      activeJobId = jobId;
      activeJobSeq = 0;
      stopJobWatcher();
      setPanel('chat');
      renderProgress([{seq:0, stage:'queued', message:`Watching background job ${jobId}`}], {job_id:jobId, status:'queued'});
      if (window.EventSource) {
        activeEventSource = new EventSource(`/api/runs/jobs/${encodeURIComponent(jobId)}/events?after=0&timeout=120`);
        activeEventSource.onmessage = event => {
          const payload = JSON.parse(event.data);
          activeJobSeq = Math.max(activeJobSeq, Number(payload.seq || 0));
          api(`/api/runs/jobs/${encodeURIComponent(jobId)}?after=${Math.max(0, activeJobSeq - 20)}`).then(job => {
            renderProgress(job.events || [], job);
            if (['completed','failed','cancelled','needs_human'].includes(job.status)) finishWatchedJob(job);
          }).catch(() => {});
        };
        activeEventSource.addEventListener('done', () => pollJobOnce(jobId));
        activeEventSource.onerror = () => {
          if (activeEventSource) activeEventSource.close();
          activeEventSource = null;
          startJobPolling(jobId);
        };
      } else {
        startJobPolling(jobId);
      }
    }
    function startJobPolling(jobId) {
      if (activePoll) return;
      activePoll = setInterval(() => pollJobOnce(jobId), 1000);
      pollJobOnce(jobId).catch(() => {});
    }
    async function pollJobOnce(jobId) {
      const job = await api(`/api/runs/jobs/${encodeURIComponent(jobId)}?after=${activeJobSeq}`);
      if ((job.events || []).length) activeJobSeq = Math.max(...job.events.map(event => Number(event.seq || 0)), activeJobSeq);
      renderProgress(job.events || [], job);
      if (['completed','failed','cancelled','needs_human'].includes(job.status)) finishWatchedJob(job);
    }
    async function finishWatchedJob(job) {
      stopJobWatcher();
      activeJobId = null;
      if (job.session_id) {
        currentSession = await api(`/api/chat/sessions/${encodeURIComponent(job.session_id)}`);
        renderMessages(currentSession);
      }
      if (job.result) renderGovernance(job.result);
      await refresh();
    }
    async function refresh() {
      const [status, sessions, runs, proposals, assets, roles, providers, stats, channels] = await Promise.all([
        api('/api/status'),
        api('/api/chat/sessions'),
        api('/api/runs?limit=20'),
        api('/api/proposals?status=pending&limit=50'),
        api('/api/assets'),
        api('/api/models/roles'),
        api('/api/models/providers'),
        api('/api/models/stats'),
        api('/api/channels')
      ]);
      $('project-kv').innerHTML = `<div>Runs</div><div>${status.counts.runs}</div><div>Pending</div><div>${status.counts.pending_proposals}</div><div>Providers</div><div>${status.counts.providers}</div>`;
      $('left-runs').innerHTML = runs.slice(0, 8).map(row => item(row.task_id, `${row.status} reward=${row.reward ?? 'n/a'} ${row.task || ''}`)).join('') || item('No runs', '');
      $('left-proposals').innerHTML = proposals.slice(0, 8).map(prop => item(prop.proposal_id, `${prop.type} ${prop.risk_level || ''}`)).join('') || item('No pending proposals', '');
      if (!currentSession) currentSession = sessions[0] ? await api(`/api/chat/sessions/${sessions[0].session_id}`) : await api('/api/chat/sessions', {method:'POST', body: JSON.stringify({title:'Web session'})});
      renderMessages(currentSession);
      renderRuns(runs);
      renderProposals(proposals);
      renderAssets(assets);
      renderModels(roles, providers, stats);
      renderChannels(channels);
      if (status.latest_run) {
        const run = await api(`/api/runs/${status.latest_run.task_id}`);
        renderGovernance(run);
      } else {
        renderGovernance(null);
      }
    }
    function renderRuns(runs) {
      $('runs-table').innerHTML = table(['Run ID','Status','Reward','Task','Created'], runs.map(row => `<tr><td><button class="ghost" onclick="openRun('${esc(row.task_id)}')">${esc(row.task_id)}</button></td><td>${badge(row.status)}</td><td>${esc(row.reward ?? '')}</td><td>${esc(row.task || '')}</td><td>${esc(row.created_at || '')}</td></tr>`));
    }
    async function openRun(id) {
      const run = await api(`/api/runs/${encodeURIComponent(id)}`);
      $('run-detail').textContent = JSON.stringify(run, null, 2);
      renderRunArtifacts(run.artifacts || {});
      renderGovernance(run);
      setPanel('runs');
    }
    function renderRunArtifacts(artifacts) {
      const commands = artifacts.commands || [];
      const diffs = artifacts.diffs || [];
      const tests = artifacts.tests || [];
      const timeline = artifacts.timeline || [];
      const commandHtml = commands.slice(0, 8).map(cmd => `<div class="artifact-card"><strong>${esc(cmd.command || cmd.type)}</strong><span>${badge(cmd.status)} return=${esc(cmd.returncode ?? '')}</span><div class="diff-block">${esc(cmd.output || '')}</div></div>`).join('');
      const diffHtml = diffs.slice(0, 4).map(diff => `<div class="artifact-card"><strong>${esc(diff.path || diff.type || 'diff')}</strong><span>${esc(diff.summary || '')}</span><div class="diff-block">${esc(diff.diff || '')}</div></div>`).join('');
      const testHtml = tests.slice(0, 8).map(test => `<div class="artifact-card"><strong>${esc(test.command || 'test')}</strong><span>${badge(test.status)}</span><div class="diff-block">${esc(test.output || '')}</div></div>`).join('');
      const timelineHtml = timeline.slice(0, 12).map(item => `<div class="event"><span>${esc(item.type)}</span><span>${esc(item.summary || '')}</span><span>${badge(item.status)}</span></div>`).join('');
      $('run-artifacts').innerHTML = `
        <div class="artifact-grid">
          <div class="artifact-card"><strong>Timeline</strong>${timelineHtml || '<span>No action timeline.</span>'}</div>
          <div class="artifact-card"><strong>Commands</strong>${commandHtml || '<span>No commands.</span>'}</div>
          <div class="artifact-card"><strong>Tests</strong>${testHtml || '<span>No tests.</span>'}</div>
        </div>
        <h3>Diffs</h3>
        ${diffHtml || '<div class="item"><strong>No diffs</strong><span></span></div>'}
      `;
    }
    async function loadJobs() {
      const jobs = await api('/api/runs/jobs');
      $('run-artifacts').innerHTML = table(['Job','Status','Session','Updated','Action'], jobs.map(job => `<tr><td>${esc(job.job_id)}</td><td>${badge(job.status)}</td><td>${esc(job.session_id || '')}</td><td>${esc(job.updated_at || '')}</td><td><button class="secondary" onclick="openJob('${esc(job.job_id)}')">Open</button></td></tr>`));
      setPanel('runs');
    }
    async function openJob(id) {
      const job = await api(`/api/runs/jobs/${encodeURIComponent(id)}`);
      $('run-detail').textContent = JSON.stringify(job, null, 2);
      renderProgress(job.events || [], job);
      setPanel('runs');
    }
    function renderProposals(proposals) {
      $('proposals-table').innerHTML = table(['ID','Type','Risk','Confidence','Title','Action'], proposals.map(prop => `<tr><td><button class="ghost" onclick="openProposal('${esc(prop.proposal_id)}')">${esc(prop.proposal_id)}</button></td><td>${esc(prop.type)}</td><td>${badge(prop.risk_level)}</td><td>${esc(prop.confidence ?? '')}</td><td>${esc(prop.title || '')}</td><td><button class="secondary" onclick="openProposal('${esc(prop.proposal_id)}')">Edit</button> <button class="secondary" onclick="acceptProposal('${esc(prop.proposal_id)}')">Accept</button> <button class="secondary" onclick="rejectProposal('${esc(prop.proposal_id)}')">Reject</button></td></tr>`));
    }
    async function openProposal(id) {
      const proposal = await api(`/api/proposals/${encodeURIComponent(id)}`);
      currentProposalId = proposal.proposal_id || id;
      $('proposal-detail').textContent = JSON.stringify(proposal, null, 2);
      $('proposal-editor').value = JSON.stringify(proposal, null, 2);
      setPanel('proposals');
    }
    async function saveProposalEdit() {
      if (!currentProposalId) throw new Error('Open a pending proposal first.');
      let proposal;
      try { proposal = JSON.parse($('proposal-editor').value); }
      catch (error) { throw new Error(`Invalid JSON: ${error.message}`); }
      if (!confirm(`Save edits to proposal ${currentProposalId}?`)) return;
      const updated = await api(`/api/proposals/${encodeURIComponent(currentProposalId)}/edit`, {method:'POST', body: JSON.stringify({confirm:true, proposal, reason:'web console edit'})});
      $('proposal-detail').textContent = JSON.stringify(updated, null, 2);
      $('proposal-editor').value = JSON.stringify(updated, null, 2);
      await refresh();
    }
    async function acceptProposal(id) {
      if (!confirm(`Accept proposal ${id}?`)) return;
      await api(`/api/proposals/${encodeURIComponent(id)}/accept`, {method:'POST', body: JSON.stringify({confirm:true})});
      await refresh();
    }
    async function rejectProposal(id) {
      const reason = prompt(`Reject proposal ${id}: reason`);
      if (!reason) return;
      await api(`/api/proposals/${encodeURIComponent(id)}/reject`, {method:'POST', body: JSON.stringify({reason})});
      await refresh();
    }
    function renderAssets(assets) {
      $('assets-table').innerHTML = table(['Path','Type','Status','Usage','Positive','Negative'], assets.map(asset => `<tr><td><button class="ghost" onclick="openAsset('${encodeURIComponent(asset.path)}')">${esc(asset.path)}</button></td><td>${esc(asset.type)}</td><td>${badge(asset.status)}</td><td>${esc(asset.usage_count ?? 0)}</td><td>${esc(asset.positive_outcome_count ?? 0)}</td><td>${esc(asset.negative_outcome_count ?? 0)}</td></tr>`));
    }
    async function openAsset(path) {
      const asset = await api(`/api/assets/${path}`);
      currentAssetPath = decodeURIComponent(path);
      $('asset-detail').textContent = JSON.stringify(asset, null, 2);
      setPanel('assets');
    }
    async function assetUsage() {
      if (!currentAssetPath) throw new Error('Open an asset first.');
      $('asset-detail').textContent = JSON.stringify(await api(`/api/assets/${encodeURIComponent(currentAssetPath)}/usage`), null, 2);
      setPanel('assets');
    }
    async function assetGraph() {
      if (!currentAssetPath) throw new Error('Open an asset first.');
      $('asset-detail').textContent = JSON.stringify(await api(`/api/assets/${encodeURIComponent(currentAssetPath)}/graph`), null, 2);
      setPanel('assets');
    }
    async function assetLifecycle(action) {
      if (!currentAssetPath) throw new Error('Open an asset first.');
      const reason = prompt(`${action} ${currentAssetPath}: reason`);
      if (reason === null) return;
      if (!confirm(`${action} asset ${currentAssetPath}?`)) return;
      const asset = await api(`/api/assets/${encodeURIComponent(currentAssetPath)}/${action}`, {
        method:'POST',
        body: JSON.stringify({confirm:true, reason: reason || `web console ${action}`})
      });
      $('asset-detail').textContent = JSON.stringify(asset, null, 2);
      await refresh();
      setPanel('assets');
    }
    function selectedReflectModes() {
      const modes = [];
      if ($('reflect-duplicates').checked) modes.push('duplicates');
      if ($('reflect-stale').checked) modes.push('stale');
      if ($('reflect-silent').checked) modes.push('silent_failures');
      if ($('reflect-harmful').checked) modes.push('harmful');
      return modes;
    }
    function renderReflectReports(reports) {
      $('reflect-table').innerHTML = table(['Reflect ID','Findings','Generated','Written','Created','Action'], reports.map(row => `<tr><td>${esc(row.reflect_id)}</td><td>${esc(row.finding_count)}</td><td>${esc(row.generated_proposal_count)}</td><td>${esc(row.written_proposal_count)}</td><td>${esc(row.created_at || '')}</td><td><button class="secondary" onclick="openReflectReport('${esc(row.reflect_id)}')">Open</button></td></tr>`));
    }
    async function loadReflectReports() {
      const reports = await api('/api/reflect/reports?limit=20');
      renderReflectReports(reports);
      return reports;
    }
    async function openReflectReport(id) {
      const report = await api(`/api/reflect/reports/${encodeURIComponent(id)}`);
      $('reflect-detail').textContent = JSON.stringify(report, null, 2);
      setPanel('reflect');
    }
    async function runReflect() {
      const staleDays = Number($('reflect-stale-days').value || 30);
      const body = {
        since: $('reflect-since').value.trim() || null,
        asset: $('reflect-asset').value.trim() || null,
        modes: selectedReflectModes(),
        stale_days: staleDays > 0 ? staleDays : null,
        write_proposals: $('reflect-write').checked
      };
      const report = await api('/api/reflect/run', {method:'POST', body: JSON.stringify(body)});
      $('reflect-detail').textContent = JSON.stringify(report, null, 2);
      await loadReflectReports();
      if (body.write_proposals) await refresh();
    }
    async function showGraphStatus() {
      $('graph-detail').textContent = JSON.stringify(await api('/api/graph/status'), null, 2);
    }
    async function rebuildGraph() {
      $('graph-detail').textContent = JSON.stringify(await api('/api/graph/rebuild', {method:'POST', body:'{}'}), null, 2);
    }
    async function explainGraph() {
      const ref = $('graph-ref').value.trim();
      if (!ref) throw new Error('graph ref is required');
      const params = new URLSearchParams({
        ref,
        depth: String(Number($('graph-depth').value || 2)),
        limit: String(Number($('graph-limit').value || 100))
      });
      $('graph-detail').textContent = JSON.stringify(await api(`/api/graph/explain?${params.toString()}`), null, 2);
    }
    async function auditCheck() {
      const body = {
        strict: $('audit-strict').value === 'true',
        rebuild_graph: $('audit-rebuild-graph').checked,
        redaction: $('audit-redaction').value
      };
      $('audit-detail').textContent = JSON.stringify(await api('/api/audit/check', {method:'POST', body: JSON.stringify(body)}), null, 2);
    }
    async function auditBundle() {
      const body = {
        limit_runs: Number($('audit-limit-runs').value || 20),
        rebuild_graph: $('audit-rebuild-graph').checked,
        redaction: $('audit-redaction').value,
        include_reflect: $('audit-include-reflect').checked
      };
      $('audit-detail').textContent = JSON.stringify(await api('/api/audit/bundle', {method:'POST', body: JSON.stringify(body)}), null, 2);
    }
    function specInputs() {
      const values = [];
      const primary = $('spec-path').value.trim();
      if (primary) values.push(primary);
      $('spec-extra').value.split(',').map(item => item.trim()).filter(Boolean).forEach(item => values.push(item));
      return values;
    }
    async function specList() {
      $('spec-detail').textContent = JSON.stringify(await api('/api/specs'), null, 2);
    }
    async function specCheck() {
      $('spec-detail').textContent = JSON.stringify(await api('/api/spec/check', {method:'POST', body: JSON.stringify({spec: $('spec-path').value.trim() || null})}), null, 2);
    }
    async function specVerify() {
      const specs = specInputs();
      const body = {
        run_id: $('spec-run').value.trim() || 'latest',
        specs: specs.length ? specs : null
      };
      $('spec-detail').textContent = JSON.stringify(await api('/api/spec/verify', {method:'POST', body: JSON.stringify(body)}), null, 2);
    }
    async function loadTools() {
      const catalog = await api('/api/tools');
      $('tools-table').innerHTML = table(['Tool','Read-only','Write approval','Safety layer','Description'], (catalog.tools || []).map(tool => `<tr><td>${esc(tool.name)}</td><td>${badge(tool.read_only ? 'yes' : 'no')}</td><td>${badge(tool.requires_write_approval ? 'required' : 'no')}</td><td>${esc(tool.safety_layer || '')}</td><td>${esc(tool.description || '')}</td></tr>`));
      $('safety-detail').textContent = JSON.stringify(catalog, null, 2);
    }
    async function loadSafetyPolicy() {
      $('safety-detail').textContent = JSON.stringify(await api('/api/safety/policy'), null, 2);
    }
    async function checkCommand() {
      const command = $('safety-command').value.trim();
      if (!command) throw new Error('command is required');
      $('safety-detail').textContent = JSON.stringify(await api('/api/safety/check-command', {method:'POST', body: JSON.stringify({command})}), null, 2);
    }
    async function checkPath() {
      const path = $('safety-path').value.trim();
      if (!path) throw new Error('path is required');
      $('safety-detail').textContent = JSON.stringify(await api('/api/safety/check-path', {method:'POST', body: JSON.stringify({path, write: $('safety-path-write').value === 'true'})}), null, 2);
    }
    function renderModels(roles, providers, stats) {
      cachedRoles = roles || [];
      cachedProviders = providers || [];
      $('models-table').innerHTML = table(['Role','Category','Mode','Provider','Model','Status'], cachedRoles.map(role => `<tr><td><button class="ghost" onclick="fillRoleForm('${esc(role.role)}')">${esc(role.role)}</button></td><td>${esc(role.category)}</td><td>${esc(role.mode)}</td><td>${esc(role.provider || '')}</td><td>${esc(role.model || '')}</td><td>${badge(role.status)}</td></tr>`));
      $('providers-table').innerHTML = table(['Provider','Type','Base URL','Key','Models'], cachedProviders.map(provider => `<tr><td><button class="ghost" onclick="fillProviderForm('${esc(provider.provider_id)}')">${esc(provider.provider_id)}</button></td><td>${esc(provider.type)}</td><td>${esc(provider.base_url || '')}</td><td>${badge(provider.api_key_status)}</td><td>${esc((provider.models || []).join(', '))}</td></tr>`));
      $('model-stats').innerHTML = table(['Task type','Target','Runs','Reward','Latency'], stats.map(row => `<tr><td>${esc(row.task_type)}</td><td>${esc(row.target)}</td><td>${esc(row.runs)}</td><td>${esc(row.average_reward ?? '')}</td><td>${esc(row.average_latency_ms ?? '')}</td></tr>`));
      const previous = $('role-edit-name').value;
      $('role-edit-name').innerHTML = cachedRoles.map(role => `<option value="${esc(role.role)}">${esc(role.role)}</option>`).join('');
      if (previous) $('role-edit-name').value = previous;
      if ($('role-edit-name').value) fillRoleForm($('role-edit-name').value, false);
    }
    function fillRoleForm(roleName, switchPanel = true) {
      const role = cachedRoles.find(item => item.role === roleName);
      if (!role) return;
      $('role-edit-name').value = role.role;
      $('role-edit-provider').value = role.provider || '';
      $('role-edit-model').value = role.model || '';
      $('role-edit-mode').value = role.mode || 'optional';
      $('role-edit-fallback').value = (role.fallback || []).join(', ');
      $('role-edit-enabled').value = role.status === 'disabled' || role.mode === 'disabled' ? 'false' : 'true';
      if (switchPanel) setPanel('models');
    }
    function fillProviderForm(providerId) {
      const provider = cachedProviders.find(item => item.provider_id === providerId);
      if (!provider) return;
      $('provider-edit-id').value = provider.provider_id || '';
      $('provider-edit-type').value = provider.type || 'openai_compatible';
      $('provider-edit-base-url').value = provider.base_url || '';
      $('provider-edit-key-env').value = provider.api_key_env || '';
      $('provider-edit-models').value = (provider.models || []).join(', ');
      $('provider-edit-timeout').value = provider.timeout_seconds || '';
      setPanel('models');
    }
    async function saveRoleRoute() {
      const role = $('role-edit-name').value;
      if (!role) throw new Error('Choose a model role first.');
      if (!confirm(`Save model route for ${role}?`)) return;
      const updated = await api(`/api/models/roles/${encodeURIComponent(role)}`, {
        method:'PATCH',
        body: JSON.stringify({
          confirm:true,
          provider: $('role-edit-provider').value.trim(),
          model: $('role-edit-model').value.trim(),
          mode: $('role-edit-mode').value,
          fallback: $('role-edit-fallback').value,
          enabled: $('role-edit-enabled').value === 'true'
        })
      });
      $('model-stats').innerHTML = `<pre>${esc(JSON.stringify(updated, null, 2))}</pre>`;
      await refresh();
      setPanel('models');
    }
    async function testRoleRoute() {
      const role = $('role-edit-name').value;
      if (!role) throw new Error('Choose a model role first.');
      $('model-stats').innerHTML = '<pre>Testing selected route...</pre>';
      $('model-stats').innerHTML = `<pre>${esc(JSON.stringify(await api('/api/models/test', {method:'POST', body: JSON.stringify({role})}), null, 2))}</pre>`;
    }
    async function saveProvider() {
      const providerId = $('provider-edit-id').value.trim();
      if (!providerId) throw new Error('provider id is required');
      if (!confirm(`Save provider ${providerId}? Secrets must stay in environment variables.`)) return;
      const timeout = Number($('provider-edit-timeout').value || 0);
      const updated = await api('/api/models/providers', {
        method:'POST',
        body: JSON.stringify({
          confirm:true,
          provider_id: providerId,
          type: $('provider-edit-type').value,
          base_url: $('provider-edit-base-url').value.trim() || null,
          api_key_env: $('provider-edit-key-env').value.trim() || null,
          models: $('provider-edit-models').value,
          timeout_seconds: timeout > 0 ? timeout : null
        })
      });
      $('model-stats').innerHTML = `<pre>${esc(JSON.stringify(updated, null, 2))}</pre>`;
      await refresh();
      setPanel('models');
    }
    function renderChannels(channels) {
      const bindings = channels?.bindings || [];
      $('channels-table').innerHTML = table(['ID','Platform','Mode','Kind','Token','Action'], bindings.map(binding => `<tr><td>${esc(binding.id)}</td><td>${esc(binding.platform)}</td><td>${esc(binding.mode)}</td><td>${esc(binding.kind)}</td><td>${badge(binding.token_env_status)}</td><td><button class="secondary" onclick="unbindChannel('${esc(binding.id)}')">Unbind</button></td></tr>`));
      $('channels-detail').textContent = JSON.stringify(channels, null, 2);
    }
    async function bindChannel() {
      const platform = $('channel-platform').value;
      const channelId = $('channel-id').value.trim();
      if (!channelId) throw new Error('channel id is required');
      if (!confirm(`Bind ${platform}:${channelId}?`)) return;
      const binding = await api('/api/channels/bind', {
        method:'POST',
        body: JSON.stringify({
          confirm:true,
          platform,
          channel_id: channelId,
          guild_id: $('channel-guild-id').value.trim() || null,
          name: $('channel-name').value.trim() || null,
          kind: $('channel-kind').value,
          mode: $('channel-mode').value,
          token_env: $('channel-token-env').value.trim() || null,
          skill: $('channel-skill').value.trim() || null,
          prompt: $('channel-prompt').value.trim() || null,
          default: $('channel-default').checked,
          require_mention: $('channel-require-mention').checked
        })
      });
      $('channels-detail').textContent = JSON.stringify(binding, null, 2);
      await refresh();
      setPanel('channels');
    }
    async function unbindChannel(id) {
      if (!confirm(`Unbind channel ${id}?`)) return;
      $('channels-detail').textContent = JSON.stringify(await api(`/api/channels/${encodeURIComponent(id)}/unbind`, {method:'POST', body: JSON.stringify({confirm:true})}), null, 2);
      await refresh();
      setPanel('channels');
    }
    function renderCiReports(reports) {
      $('ci-table').innerHTML = table(['Report','Kind','Status','Run','Created','Action'], reports.map(report => `<tr><td>${esc(report.report_id)}</td><td>${esc(report.kind)}</td><td>${badge(report.status)}</td><td>${esc(report.run_id || '')}</td><td>${esc(report.created_at || '')}</td><td><button class="secondary" onclick="openCiReport('${esc(report.report_id)}')">Open</button></td></tr>`));
    }
    async function loadCiReports() {
      const reports = await api('/api/ci/reports?limit=50');
      renderCiReports(reports);
      $('ci-detail').textContent = JSON.stringify(reports, null, 2);
      setPanel('ci');
    }
    async function openCiReport(id) {
      const report = await api(`/api/ci/reports/${encodeURIComponent(id)}`);
      $('ci-detail').textContent = JSON.stringify(report, null, 2);
      setPanel('ci');
    }
    async function loadRepos() {
      const payload = await api('/api/repos');
      $('repos-table').innerHTML = table(['Repo','State','Runs','Pending','CI'], (payload.repos || []).map(repo => `<tr><td>${esc(repo.name)}${repo.current ? ' current' : ''}<br><span class="subtle">${esc(repo.root)}</span></td><td>${badge(repo.config_exists ? 'configured' : 'missing')}</td><td>${esc(repo.runs)}</td><td>${esc(repo.pending_proposals)}</td><td>${esc(repo.ci_reports)}</td></tr>`));
      $('repos-detail').textContent = JSON.stringify(payload, null, 2);
      setPanel('repos');
    }
    async function runTask() {
      const session = await ensureSession();
      const task = $('task').value.trim();
      if (!task) return;
      currentSession.messages.push({role:'user', content:task});
      currentSession.messages.push({role:'assistant', content:'Running...'});
      renderMessages(currentSession);
      const body = {
        task,
        test_commands: $('tests').value.trim() ? [$('tests').value.trim()] : [],
        spec: $('spec').value.trim() || null,
        workspace_mode: $('workspace').value,
        max_steps: Number($('steps').value || 10),
        dry_run: $('dry-run').checked,
        allow_shell: $('allow-shell').checked,
        model_role_override: $('model-override').value.trim() || null
      };
      const result = await api(`/api/chat/sessions/${session.session_id}/message-async`, {method:'POST', body: JSON.stringify(body)});
      currentSession = result.session;
      renderMessages(currentSession);
      watchJob(result.job.job_id);
    }
    async function retryLast() {
      const session = await ensureSession();
      if (!confirm('Retry the latest user task in this session?')) return;
      currentSession.messages.push({role:'assistant', content:'Retrying last task...'});
      renderMessages(currentSession);
      const result = await api(`/api/chat/sessions/${session.session_id}/retry-async`, {
        method:'POST',
        body: JSON.stringify({
          test_commands: $('tests').value.trim() ? [$('tests').value.trim()] : [],
          spec: $('spec').value.trim() || null,
          max_steps: Number($('steps').value || 10),
          dry_run: $('dry-run').checked,
          allow_shell: $('allow-shell').checked,
          model_role_override: $('model-override').value.trim() || null
        })
      });
      currentSession = result.session;
      renderMessages(currentSession);
      watchJob(result.job.job_id);
    }
    async function stopSession() {
      const session = await ensureSession();
      const result = activeJobId
        ? await api(`/api/runs/jobs/${encodeURIComponent(activeJobId)}/cancel`, {method:'POST', body: JSON.stringify({reason:'web console stop button'})})
        : await api(`/api/chat/sessions/${session.session_id}/stop`, {method:'POST', body: JSON.stringify({reason:'web console stop button'})});
      if (result.session) currentSession = result.session;
      if (result.job) renderProgress([], result.job);
      if (currentSession) renderMessages(currentSession);
    }
    $('run').onclick = () => runTask().catch(error => alert(error.message));
    $('retry-last').onclick = () => retryLast().catch(error => alert(error.message));
    $('stop-session').onclick = () => stopSession().catch(error => alert(error.message));
    $('load-jobs').onclick = () => loadJobs().catch(error => alert(error.message));
    $('refresh').onclick = () => refresh().catch(error => alert(error.message));
    $('new-session').onclick = async () => {
      currentSession = await api('/api/chat/sessions', {method:'POST', body: JSON.stringify({title:'Web session'})});
      renderMessages(currentSession);
    };
    $('test-models').onclick = async () => {
      $('model-stats').innerHTML = '<pre>Testing routes...</pre>';
      try { $('model-stats').innerHTML = `<pre>${esc(JSON.stringify(await api('/api/models/test-all', {method:'POST', body:'{}'}), null, 2))}</pre>`; }
      catch (error) { $('model-stats').innerHTML = `<pre>${esc(error.message)}</pre>`; }
    };
    $('role-edit-name').onchange = () => fillRoleForm($('role-edit-name').value);
    $('save-role-route').onclick = () => saveRoleRoute().catch(error => alert(error.message));
    $('test-role-route').onclick = () => testRoleRoute().catch(error => alert(error.message));
    $('save-provider').onclick = () => saveProvider().catch(error => alert(error.message));
    $('run-reflect').onclick = () => runReflect().catch(error => $('reflect-detail').textContent = error.message);
    $('load-reflect').onclick = () => loadReflectReports().catch(error => $('reflect-detail').textContent = error.message);
    $('save-proposal-edit').onclick = () => saveProposalEdit().catch(error => alert(error.message));
    $('graph-status').onclick = () => showGraphStatus().catch(error => $('graph-detail').textContent = error.message);
    $('rebuild-graph').onclick = () => rebuildGraph().catch(error => $('graph-detail').textContent = error.message);
    $('explain-graph').onclick = () => explainGraph().catch(error => $('graph-detail').textContent = error.message);
    $('audit-check').onclick = () => auditCheck().catch(error => $('audit-detail').textContent = error.message);
    $('audit-bundle').onclick = () => auditBundle().catch(error => $('audit-detail').textContent = error.message);
    $('spec-list').onclick = () => specList().catch(error => $('spec-detail').textContent = error.message);
    $('spec-check').onclick = () => specCheck().catch(error => $('spec-detail').textContent = error.message);
    $('spec-verify').onclick = () => specVerify().catch(error => $('spec-detail').textContent = error.message);
    $('load-tools').onclick = () => loadTools().catch(error => $('safety-detail').textContent = error.message);
    $('load-safety').onclick = () => loadSafetyPolicy().catch(error => $('safety-detail').textContent = error.message);
    $('check-command').onclick = () => checkCommand().catch(error => $('safety-detail').textContent = error.message);
    $('check-path').onclick = () => checkPath().catch(error => $('safety-detail').textContent = error.message);
    $('asset-usage').onclick = () => assetUsage().catch(error => alert(error.message));
    $('asset-graph').onclick = () => assetGraph().catch(error => alert(error.message));
    $('asset-deprecate').onclick = () => assetLifecycle('deprecate').catch(error => alert(error.message));
    $('asset-archive').onclick = () => assetLifecycle('archive').catch(error => alert(error.message));
    $('asset-reactivate').onclick = () => assetLifecycle('reactivate').catch(error => alert(error.message));
    $('bind-channel').onclick = () => bindChannel().catch(error => $('channels-detail').textContent = error.message);
    $('load-ci').onclick = () => loadCiReports().catch(error => $('ci-detail').textContent = error.message);
    $('load-repos').onclick = () => loadRepos().catch(error => $('repos-detail').textContent = error.message);
    document.querySelectorAll('nav button').forEach(btn => btn.onclick = () => setPanel(btn.dataset.panel));
    refresh().catch(error => {
      $('messages').innerHTML = `<div class="message assistant">${esc(error.message)}</div>`;
    });
  </script>
</body>
</html>
"""
