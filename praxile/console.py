from __future__ import annotations

from dataclasses import dataclass
from html import escape

from .config import Config


@dataclass(frozen=True)
class ConsolePage:
    html: str


def render_console(config: Config) -> ConsolePage:
    project = escape(str(config.paths.root))
    state = escape(str(config.paths.state))
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Praxile Console</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --text: #161616;
      --muted: #666b70;
      --line: #d9d9d2;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --danger: #b42318;
      --code: #272822;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.4;
    }}
    header {{
      border-bottom: 1px solid var(--line);
      background: #fff;
    }}
    .bar {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 18px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{
      margin: 0;
      font-size: 22px;
      line-height: 1.1;
    }}
    .meta {{
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 20px;
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
      gap: 16px;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-width: 0;
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 16px;
    }}
    label {{
      display: block;
      margin: 10px 0 6px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
    }}
    textarea, input, select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 11px;
      font: inherit;
      background: #fff;
      color: var(--text);
    }}
    textarea {{
      min-height: 120px;
      resize: vertical;
    }}
    .row {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }}
    .actions {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 12px;
    }}
    button {{
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      padding: 9px 12px;
      font: inherit;
      cursor: pointer;
    }}
    button.secondary {{
      background: #fff;
      color: var(--accent-strong);
    }}
    button.danger {{
      background: var(--danger);
      border-color: var(--danger);
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: var(--code);
      color: #f8f8f2;
      border-radius: 8px;
      padding: 12px;
      min-height: 180px;
      max-height: 520px;
      overflow: auto;
      font-size: 13px;
    }}
    .list {{
      display: grid;
      gap: 8px;
    }}
    .item {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
    }}
    .item strong {{
      display: block;
      overflow-wrap: anywhere;
    }}
    .item span {{
      color: var(--muted);
      font-size: 13px;
    }}
    @media (max-width: 860px) {{
      main {{
        grid-template-columns: 1fr;
      }}
      .bar, .row {{
        display: block;
      }}
      .bar > * + *, .row > * + * {{
        margin-top: 10px;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="bar">
      <div>
        <h1>Praxile Console</h1>
        <div class="meta">{project}</div>
      </div>
      <div class="meta">State: {state}</div>
    </div>
  </header>
  <main>
    <div class="list">
      <section>
        <h2>Task</h2>
        <label for="task">Task</label>
        <textarea id="task" placeholder="Fix the failing parser test"></textarea>
        <div class="row">
          <div>
            <label for="tests">Test commands</label>
            <input id="tests" placeholder="python -m pytest">
          </div>
          <div>
            <label for="steps">Max steps</label>
            <input id="steps" type="number" min="1" max="50" value="10">
          </div>
        </div>
        <div class="actions">
          <button id="run">Run</button>
          <button class="secondary" id="refresh">Refresh</button>
        </div>
      </section>
      <section>
        <h2>Review</h2>
        <div class="row">
          <div>
            <label for="review-id">Task or proposal ID</label>
            <input id="review-id" placeholder="leave blank for latest">
          </div>
          <div>
            <label for="proposal-id">Proposal ID</label>
            <input id="proposal-id" placeholder="proposal_...">
          </div>
        </div>
        <div class="actions">
          <button class="secondary" id="review">Review</button>
          <button id="accept">Accept</button>
        </div>
      </section>
      <section>
        <h2>Output</h2>
        <pre id="output">Ready.</pre>
      </section>
    </div>
    <div class="list">
      <section>
        <h2>History</h2>
        <div id="history" class="list"></div>
      </section>
      <section>
        <h2>Channels</h2>
        <div id="channels" class="list"></div>
        <div class="row">
          <div>
            <label for="platform">Platform</label>
            <select id="platform">
              <option value="telegram">telegram</option>
              <option value="discord">discord</option>
            </select>
          </div>
          <div>
            <label for="channel-id">Channel ID</label>
            <input id="channel-id" placeholder="-100123 or 123456">
          </div>
        </div>
        <div class="row">
          <div>
            <label for="guild-id">Discord guild ID</label>
            <input id="guild-id" placeholder="optional">
          </div>
          <div>
            <label for="token-env">Token env</label>
            <input id="token-env" placeholder="TELEGRAM_BOT_TOKEN">
          </div>
        </div>
        <div class="actions">
          <button class="secondary" id="bind">Bind Channel</button>
        </div>
      </section>
    </div>
  </main>
  <script>
    const out = document.getElementById('output');
    const show = value => {{
      out.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    }};
    async function api(path, options = {{}}) {{
      const response = await fetch(path, {{
        ...options,
        headers: {{
          'Content-Type': 'application/json',
          ...(options.headers || {{}})
        }}
      }});
      const data = await response.json();
      if (!data.ok) throw new Error(data.error || 'Request failed');
      return data.result;
    }}
    function item(title, body) {{
      const node = document.createElement('div');
      node.className = 'item';
      node.innerHTML = `<strong>${{title}}</strong><span>${{body}}</span>`;
      return node;
    }}
    async function refresh() {{
      const history = await api('/history?limit=8');
      const channels = await api('/channels');
      const historyEl = document.getElementById('history');
      historyEl.textContent = '';
      if (!history.length) historyEl.append(item('No task history yet', 'Run a task to create a trajectory.'));
      history.forEach(row => historyEl.append(item(row.task_id, `${{row.status}} reward=${{row.reward_score}} ${{
row.user_task}}`)));
      const channelsEl = document.getElementById('channels');
      channelsEl.textContent = '';
      if (!channels.length) channelsEl.append(item('No bindings', 'Bind Telegram or Discord below.'));
      channels.forEach(binding => channelsEl.append(item(binding.id, `${{binding.platform}} ${{
binding.mode}} token_env=${{binding.token_env}}`)));
    }}
    document.getElementById('refresh').onclick = async () => {{
      try {{ await refresh(); show('Refreshed.'); }} catch (error) {{ show(error.message); }}
    }};
    document.getElementById('run').onclick = async () => {{
      const task = document.getElementById('task').value.trim();
      const tests = document.getElementById('tests').value.split(',').map(v => v.trim()).filter(Boolean);
      const maxSteps = Number(document.getElementById('steps').value || 10);
      try {{
        show('Running...');
        const result = await api('/run', {{
          method: 'POST',
          body: JSON.stringify({{task, test_commands: tests, max_steps: maxSteps}})
        }});
        show(result);
        await refresh();
      }} catch (error) {{ show(error.message); }}
    }};
    document.getElementById('review').onclick = async () => {{
      const id = document.getElementById('review-id').value.trim();
      try {{ show(await api(id ? `/review?id=${{encodeURIComponent(id)}}` : '/review')); }}
      catch (error) {{ show(error.message); }}
    }};
    document.getElementById('accept').onclick = async () => {{
      const proposalId = document.getElementById('proposal-id').value.trim();
      try {{
        show(await api('/accept', {{method: 'POST', body: JSON.stringify({{proposal_id: proposalId}})}}));
        await refresh();
      }} catch (error) {{ show(error.message); }}
    }};
    document.getElementById('bind').onclick = async () => {{
      const platform = document.getElementById('platform').value;
      const channelId = document.getElementById('channel-id').value.trim();
      const guildId = document.getElementById('guild-id').value.trim();
      const tokenEnv = document.getElementById('token-env').value.trim();
      try {{
        show(await api('/channels/bind', {{
          method: 'POST',
          body: JSON.stringify({{
            platform,
            channel_id: channelId,
            guild_id: guildId || null,
            token_env: tokenEnv || null
          }})
        }}));
        await refresh();
      }} catch (error) {{ show(error.message); }}
    }};
    refresh().catch(error => show(error.message));
  </script>
</body>
</html>
"""
    return ConsolePage(html)
