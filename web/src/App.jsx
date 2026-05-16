import { useEffect, useMemo, useState } from "react";
import { GitPullRequest, Network, RefreshCw, Send, Sparkles } from "lucide-react";
import { api } from "./api.js";

const panels = ["chat", "runs", "proposals", "graph", "ci"];

export function App() {
  const [panel, setPanel] = useState("chat");
  const [status, setStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [proposals, setProposals] = useState([]);
  const [detail, setDetail] = useState(null);
  const [task, setTask] = useState("");
  const [selectedProposal, setSelectedProposal] = useState(null);
  const [graph, setGraph] = useState(null);
  const [ciReports, setCiReports] = useState([]);
  const [githubContext, setGithubContext] = useState(null);
  const [error, setError] = useState("");

  async function refresh() {
    setError("");
    const [nextStatus, nextRuns, nextProposals, nextCi, nextGh] = await Promise.all([
      api("/api/status"),
      api("/api/runs?limit=30"),
      api("/api/proposals?status=pending&limit=50"),
      api("/api/ci/reports?limit=30"),
      api("/api/github/context")
    ]);
    setStatus(nextStatus);
    setRuns(nextRuns);
    setProposals(nextProposals);
    setCiReports(nextCi);
    setGithubContext(nextGh);
  }

  useEffect(() => {
    refresh().catch((err) => setError(err.message));
  }, []);

  async function runTask() {
    if (!task.trim()) return;
    const session = await api("/api/chat/sessions", { method: "POST", body: JSON.stringify({ title: "React console" }) });
    const result = await api(`/api/chat/sessions/${session.session_id}/message-async`, {
      method: "POST",
      body: JSON.stringify({ task, dry_run: false, max_steps: 10 })
    });
    setDetail(result.job);
    setPanel("runs");
    setTask("");
  }

  async function openRun(runId) {
    const run = await api(`/api/runs/${encodeURIComponent(runId)}`);
    setDetail(run);
    setPanel("runs");
  }

  async function openProposal(id) {
    const proposal = await api(`/api/proposals/${encodeURIComponent(id)}`);
    setSelectedProposal(proposal);
    setPanel("proposals");
  }

  async function saveProposal() {
    if (!selectedProposal) return;
    const updated = await api(`/api/proposals/${encodeURIComponent(selectedProposal.proposal_id)}/edit`, {
      method: "POST",
      body: JSON.stringify({ confirm: true, proposal: selectedProposal, reason: "react console structured edit" })
    });
    setSelectedProposal(updated);
    await refresh();
  }

  async function loadGraph(ref) {
    if (!ref) return;
    const nextGraph = await api(`/api/graph/view?${new URLSearchParams({ ref, depth: "2", limit: "120" })}`);
    setGraph(nextGraph);
    setPanel("graph");
  }

  async function publishPrComment(reportId) {
    const pr = Number(prompt("PR number", githubContext?.default_pr_number || ""));
    if (!pr) return;
    if (!confirm(`Publish ${reportId} as a GitHub PR comment?`)) return;
    const result = await api("/api/github/pr-comments", {
      method: "POST",
      body: JSON.stringify({ confirm: true, report_id: reportId, pr_number: pr, repository: githubContext?.repository })
    });
    setDetail(result);
  }

  async function importArtifacts() {
    const runId = prompt("GitHub Actions run id", githubContext?.actions_run_id || "");
    if (!runId) return;
    if (!confirm(`Import artifacts for Actions run ${runId}?`)) return;
    const result = await api("/api/github/actions/artifacts/import", {
      method: "POST",
      body: JSON.stringify({ confirm: true, run_id: runId, repository: githubContext?.repository })
    });
    setDetail(result);
    await refresh();
  }

  const graphView = useMemo(() => graph?.view || { nodes: [], edges: [] }, [graph]);

  return (
    <div className="app">
      <aside>
        <div className="brand"><Sparkles size={18} /> Praxile</div>
        <p>{status?.project?.root || "Loading project..."}</p>
        <nav>
          {panels.map((name) => (
            <button key={name} className={panel === name ? "active" : ""} onClick={() => setPanel(name)}>{name}</button>
          ))}
        </nav>
        <button onClick={() => refresh().catch((err) => setError(err.message))}><RefreshCw size={15} /> Refresh</button>
      </aside>
      <main>
        {error && <div className="notice danger">{error}</div>}
        {panel === "chat" && (
          <section>
            <h1>Chat Workspace</h1>
            <div className="composer">
              <textarea value={task} onChange={(event) => setTask(event.target.value)} placeholder="Ask Praxile to fix a bug or inspect a project risk" />
              <button onClick={() => runTask().catch((err) => setError(err.message))}><Send size={15} /> Run</button>
            </div>
          </section>
        )}
        {panel === "runs" && (
          <section>
            <h1>Runs</h1>
            <div className="grid">
              {runs.map((run) => <Card key={run.task_id} title={run.task_id} meta={`${run.status} · reward ${run.reward ?? "n/a"}`} onClick={() => openRun(run.task_id)} />)}
            </div>
            <JsonDetail value={detail} />
          </section>
        )}
        {panel === "proposals" && (
          <section>
            <h1>Structured Proposal Editor</h1>
            <div className="grid">
              {proposals.map((proposal) => <Card key={proposal.proposal_id} title={proposal.title || proposal.proposal_id} meta={`${proposal.type} · ${proposal.risk_level}`} onClick={() => openProposal(proposal.proposal_id)} />)}
            </div>
            {selectedProposal && <ProposalEditor proposal={selectedProposal} setProposal={setSelectedProposal} save={saveProposal} />}
          </section>
        )}
        {panel === "graph" && (
          <section>
            <h1>Experience Graph</h1>
            <button onClick={() => loadGraph(status?.latest_run?.task_id).catch((err) => setError(err.message))}><Network size={15} /> Latest run graph</button>
            <GraphView view={graphView} />
            <JsonDetail value={graph} />
          </section>
        )}
        {panel === "ci" && (
          <section>
            <h1>CI / PR</h1>
            <div className="toolbar">
              <button onClick={() => importArtifacts().catch((err) => setError(err.message))}>Import Actions artifacts</button>
            </div>
            <div className="grid">
              {ciReports.map((report) => <Card key={report.report_id} title={report.report_id} meta={`${report.status} · ${report.run_id || ""}`} onClick={() => publishPrComment(report.report_id)} icon={<GitPullRequest size={15} />} />)}
            </div>
            <JsonDetail value={detail || githubContext} />
          </section>
        )}
      </main>
    </div>
  );
}

function Card({ title, meta, onClick, icon = null }) {
  return <button className="card" onClick={onClick}>{icon}<strong>{title}</strong><span>{meta}</span></button>;
}

function ProposalEditor({ proposal, setProposal, save }) {
  return (
    <div className="editor">
      <input value={proposal.title || ""} onChange={(event) => setProposal({ ...proposal, title: event.target.value })} />
      <textarea value={proposal.reason || ""} onChange={(event) => setProposal({ ...proposal, reason: event.target.value })} />
      <textarea value={(proposal.evidence || []).join("\n")} onChange={(event) => setProposal({ ...proposal, evidence: event.target.value.split("\n").filter(Boolean) })} />
      <textarea className="code" value={JSON.stringify(proposal.changes || [], null, 2)} onChange={(event) => {
        try { setProposal({ ...proposal, changes: JSON.parse(event.target.value || "[]") }); } catch { setProposal({ ...proposal, _invalidChanges: event.target.value }); }
      }} />
      <button onClick={() => save().catch((err) => alert(err.message))}>Save structured edit</button>
    </div>
  );
}

function GraphView({ view }) {
  const nodes = view.nodes || [];
  const byId = Object.fromEntries(nodes.map((node) => [node.node_id, node]));
  return (
    <div className="graph">
      <svg viewBox={`0 0 ${view.width || 920} ${view.height || 360}`}>
        {(view.edges || []).map((edge) => {
          const source = byId[edge.source];
          const target = byId[edge.target];
          if (!source || !target) return null;
          return <line key={edge.edge_id} x1={source.x} y1={source.y} x2={target.x} y2={target.y} stroke="#aab3bf" />;
        })}
        {nodes.map((node) => (
          <g key={node.node_id}>
            <circle cx={node.x} cy={node.y} r="18" fill={node.color || "#64748b"} />
            <text x={node.x + 26} y={node.y} fontSize="12">{node.label}</text>
            <text x={node.x + 26} y={node.y + 14} fontSize="10" fill="#697586">{node.node_type}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function JsonDetail({ value }) {
  return <pre>{value ? JSON.stringify(value, null, 2) : "No detail selected."}</pre>;
}
