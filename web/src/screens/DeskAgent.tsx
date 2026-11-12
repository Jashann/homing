import { useEffect, useRef, useState } from "react";
import { getAgentRun, getSavedAgentRuns, startAgent } from "../api";
import type { AgentRun, AgentStep, AgentTask } from "../types";
import { Problem } from "../ui";

// The desk agent: the model picks the next tool call, code runs it and decides whether it is allowed.
// A rehearsal (?rehearsal=1 in the address) runs the same gate but sends no money.
const REHEARSAL = new URLSearchParams(window.location.search).get("rehearsal") === "1";

const time = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";

const SECTION: Record<string, string> = {
  a: "Payouts imported from PayPal",
  b: "Same name on more than one balance",
  c: "Turned-down claims",
  d: "Claims ready to settle",
  e: "Asked to pay a turned-down claim",
};

function args(a: Record<string, unknown> | null) {
  if (!a) return "";
  return Object.entries(a)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(", ");
}

function Step({ s }: { s: AgentStep }) {
  const tone = s.outcome === "refused" || s.outcome === "error" ? "bad" : s.outcome === "allowed" ? "good" : "neutral";
  return (
    <li className={`agent-step agent-step-${tone}`} data-tool={s.tool || ""} data-outcome={s.outcome}>
      <span className="agent-time">{time(s.started_at)}</span>
      <div className="agent-body">
        {s.say && <p className="agent-say">{s.say}</p>}
        {s.tool && (
          <code className="agent-call">
            {s.tool}({args(s.args)})
          </code>
        )}
        <p className={`agent-result agent-result-${tone}`}>
          {s.outcome === "running" ? "Working..." : s.result}
        </p>
      </div>
      <span className={`agent-verdict agent-verdict-${tone}`}>
        {s.outcome === "allowed" ? "Allowed" : s.outcome === "refused" ? "Refused by code" : s.outcome === "error" ? "Failed" : "Running"}
      </span>
    </li>
  );
}

function Task({ t }: { t: AgentTask }) {
  return (
    <div className={`agent-task agent-task-${t.kind}`} data-kind={t.kind}>
      <h3 className="agent-task-title">{t.title}</h3>
      {t.steps.length === 0 && t.status === "waiting" && <p className="muted">Waiting its turn.</p>}
      {t.steps.length === 0 && t.status === "running" && <p className="muted">Thinking...</p>}
      <ol className="agent-steps">
        {t.steps.map((s) => (
          <Step key={s.n} s={s} />
        ))}
      </ol>
      {t.status === "running" && t.steps.length > 0 && <p className="muted agent-thinking">Thinking about the next step...</p>}
      {t.summary && t.status !== "running" && <p className="agent-task-summary">{t.summary}</p>}
    </div>
  );
}

function Trace({ run }: { run: AgentRun }) {
  const groups: string[] = [];
  for (const t of run.tasks) if (!groups.includes(t.group)) groups.push(t.group);
  return (
    <div className="agent-trace">
      <p className="agent-label">
        <span className={run.label === "Live run" ? "chip chip-accent" : "chip chip-neutral"}>{run.label}</span>
        {run.rehearsal && <span className="chip chip-warn">Rehearsal: no money is sent</span>}
        <span className="muted"> started {time(run.started_at)}{run.ended_at ? `, finished ${time(run.ended_at)}` : ""}</span>
      </p>
      {run.note && <p className="muted agent-note">{run.note}</p>}
      {run.tasks.length === 0 && <p className="muted">There was no open work on the desk.</p>}
      {groups.map((g) => (
        <section key={g} className="agent-section" data-group={g}>
          <h2 className="section-title">{SECTION[g] || g}</h2>
          {run.tasks.filter((t) => t.group === g).map((t, i) => (
            <Task key={i} t={t} />
          ))}
        </section>
      ))}
      {run.summary && run.status === "done" && (
        <div className="agent-summary card" data-testid="agent-summary">
          <p className="label">{run.mode === "brakes" ? "This request" : "What the desk agent did"}</p>
          <div className="agent-counts">
            <div><b>{run.summary.found}</b><span>found</span></div>
            <div><b>{run.summary.held}</b><span>held</span></div>
            <div><b>{run.summary.sent_to_person}</b><span>sent to a person</span></div>
            <div><b>{run.summary.paid}</b><span>paid</span></div>
            <div className="bad"><b>{run.summary.refused}</b><span>refused by code</span></div>
          </div>
          <p className="muted">
            {run.summary.tool_calls === 1
              ? "1 tool call, logged. The model chose it; code decided it."
              : `${run.summary.tool_calls} tool calls, every one logged. The model chose them; code decided each one.`}
          </p>
        </div>
      )}
    </div>
  );
}

export default function DeskAgent({ business }: { business: string }) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [problem, setProblem] = useState("");
  const timer = useRef<number | null>(null);

  const follow = (id: string) => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(async () => {
      try {
        const r = await getAgentRun(id);
        setRun(r);
        if (r.status === "done" && timer.current) window.clearInterval(timer.current);
      } catch {
        // keep asking
      }
    }, 800);
  };
  useEffect(() => () => {
    if (timer.current) window.clearInterval(timer.current);
  }, []);

  const go = async (mode: "queue" | "brakes") => {
    setProblem("");
    try {
      const r = await startAgent(mode, REHEARSAL);
      setRun(r);
      if (r.status !== "done") follow(r.id);
    } catch (e) {
      setProblem((e as Error).message);
    }
  };
  const saved = async (mode: "queue" | "brakes") => {
    setProblem("");
    try {
      const s = await getSavedAgentRuns();
      const r = s[mode];
      if (r) setRun({ ...r, label: "Saved run" });
      else setProblem("There is no saved run of that kind.");
    } catch (e) {
      setProblem((e as Error).message);
    }
  };
  const busy = run?.status === "running";

  return (
    <div className="desk-agent">
      <div className="card agent-intro">
        <p>
          The desk agent works {business}'s open queue with real tools: it asks PayPal about each payout, looks up the
          ledger, holds balances, drafts notices, sends doubtful cases to a person and pays approved claims through PayPal
          Payouts. The model chooses each step. Code runs it and refuses anything the rules do not allow.
        </p>
        <div className="agent-buttons">
          <button className="button" onClick={() => go("queue")} disabled={busy}>
            {busy && run?.mode === "queue" ? "Working the queue..." : "Work the queue"}
          </button>
          <button className="button-secondary" onClick={() => go("brakes")} disabled={busy}>
            Ask it to pay a turned-down claim
          </button>
          <button className="button-quiet" onClick={() => saved("queue")} disabled={busy}>
            Show the saved run
          </button>
        </div>
      </div>
      {problem && <Problem text={problem} />}
      {run && <Trace run={run} />}
    </div>
  );
}
