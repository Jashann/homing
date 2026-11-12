import { Fragment, useCallback, useEffect, useState } from "react";
import { approveClaim, draftNotice, getClaims, getLedger, getScoreboard, importFromPayPal, resetDemo } from "../api";
import type { Balance, BalanceStatus, Claim, ImportResult, Notice, Scoreboard } from "../types";
import { BALANCE_WORDS, count, isEnglish, languageName, longDate, money, sentence, shortDate } from "../format";
import { Chip, ChecksTable, DocumentsRead, Empty, FALLBACK_META, Loading, Page, PlainList, Problem, useMeta } from "../ui";

import DeskAgent from "./DeskAgent";

export type HolderTab = "balances" | "approvals" | "agent" | "invoices" | "results";

const FILTERS: (BalanceStatus | "")[] = ["", "DORMANT", "MATCHED", "PROVEN", "PAYING", "PAID", "UNCLAIMED", "RETURNED", "ESCHEATED"];

const STATUS_TONE: Record<BalanceStatus, "good" | "warn" | "bad" | "neutral" | "accent"> = {
  DORMANT: "neutral",
  MATCHED: "accent",
  PROVEN: "accent",
  PAYING: "accent",
  PAID: "good",
  UNCLAIMED: "warn",
  RETURNED: "warn",
  ESCHEATED: "bad",
};

const waitingForPerson = (c: Claim) => c.proof.verdict === "NEEDS_REVIEW" && !c.proof.approved_by;

export default function Holder({ tab, onTab }: { tab: HolderTab; onTab: (t: HolderTab) => void }) {
  const meta = useMeta();
  const named = meta.holder.name !== FALLBACK_META.holder.name;
  const [claims, setClaims] = useState<Claim[] | null>(null);
  const [claimsProblem, setClaimsProblem] = useState("");

  const loadClaims = useCallback(() => {
    setClaimsProblem("");
    getClaims()
      .then((r) => setClaims(r.claims))
      .catch((e: Error) => setClaimsProblem(e.message));
  }, []);
  useEffect(loadClaims, [loadClaims, tab]);

  const [round, setRound] = useState(0);
  const [resetting, setResetting] = useState(false);
  const [resetProblem, setResetProblem] = useState("");
  const [asking, setAsking] = useState(false);
  const startAgain = async () => {
    setAsking(false);
    setResetting(true);
    setResetProblem("");
    try {
      await resetDemo();
      setClaims(null);
      loadClaims();
      setRound((r) => r + 1);
    } catch (e) {
      setResetProblem(`We could not start again. ${(e as Error).message}`);
    } finally {
      setResetting(false);
    }
  };

  const waiting = claims?.filter(waitingForPerson).length ?? 0;
  const tabs: [HolderTab, string][] = [
    ["balances", "Balances"],
    ["approvals", waiting ? `Waiting for approval (${waiting})` : "Waiting for approval"],
    ["agent", "Desk agent"],
    ["invoices", "Invoices"],
    ["results", "Results"],
  ];

  return (
    <Page wide>
      <div className="screen-head screen-head-compact">
        <p className="context-line">
          {named ? `${meta.holder.name}, ${meta.holder.kind}` : "The business holding the money"}
        </p>
        <h1 className="title title-small">Payouts waiting for their owners</h1>
        <p className="lead">Approve claims that need a person, see who was paid, and invoice the fee.</p>
      </div>
      <div className="tabs" role="tablist">
        {tabs.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "tab on" : "tab"} onClick={() => onTab(id)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "balances" && <Balances key={round} business={named ? meta.holder.name : "the business"} />}
      {tab === "approvals" && <Approvals claims={claims} problem={claimsProblem} reload={loadClaims} onChange={setClaims} />}
      {tab === "agent" && <DeskAgent key={round} business={named ? meta.holder.name : "the business"} />}
      {tab === "invoices" && <Invoices claims={claims} problem={claimsProblem} reload={loadClaims} business={named ? meta.holder.name : "The business"} />}
      {tab === "results" && <Results key={round} />}
      <footer className="demo-tools">
        <span className="demo-tools-label">Demo tools</span>
        {asking ? (
          <span className="start-again-ask" role="group" aria-label="Start again">
            <span>Clear every sample claim and put the sample balances back? Payments already made in PayPal's test system stay there.</span>
            <button className="button-quiet" onClick={startAgain}>
              Yes, start again
            </button>
            <button className="button-quiet" onClick={() => setAsking(false)}>
              Keep everything
            </button>
          </span>
        ) : (
          <button className="button-quiet" onClick={() => setAsking(true)} disabled={resetting}>
            {resetting ? "Starting again..." : "Start again"}
          </button>
        )}
        {resetProblem && <Problem text={resetProblem} />}
      </footer>
    </Page>
  );
}

function Balances({ business }: { business: string }) {
  const [filter, setFilter] = useState<BalanceStatus | "">("");
  const [rows, setRows] = useState<Balance[] | null>(null);
  const [problem, setProblem] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [importing, setImporting] = useState(false);
  const [imported, setImported] = useState<ImportResult | null>(null);
  const [importProblem, setImportProblem] = useState("");
  const importNow = async () => {
    setImporting(true);
    setImportProblem("");
    try {
      const r = await importFromPayPal();
      setImported(r);
      setAttempt((a) => a + 1);
    } catch (e) {
      setImportProblem(`The payouts could not be imported. ${(e as Error).message}`);
    } finally {
      setImporting(false);
    }
  };
  const [notices, setNotices] = useState<Record<string, Notice | "busy" | { error: string }>>({});

  useEffect(() => {
    setRows(null);
    setProblem("");
    getLedger(filter || undefined)
      // Payouts imported from PayPal come first: they are the newest uncollected money.
      .then((r) => setRows([...r.balances].sort((a, b) => Number(Boolean(b.paypal)) - Number(Boolean(a.paypal)))))
      .catch((e: Error) => setProblem(e.message));
  }, [filter, attempt]);

  const draft = async (id: string) => {
    setNotices((s) => ({ ...s, [id]: "busy" }));
    try {
      const r = await draftNotice(id);
      setNotices((s) => ({ ...s, [id]: r }));
    } catch (e) {
      setNotices((s) => ({ ...s, [id]: { error: `The notice could not be drafted. ${(e as Error).message}` } }));
    }
  };

  const total = rows?.reduce((s, r) => s + Number(r.amount), 0) ?? 0;

  return (
    <section>
      <div className="import-row">
        <button className="button-secondary" onClick={importNow} disabled={importing}>
          {importing ? "Asking PayPal..." : "Import uncollected payouts from PayPal"}
        </button>
        {imported && (
          <p className="muted" role="status">
            PayPal checked {imported.items_checked} {imported.items_checked === 1 ? "payout" : "payouts"} from {business}'s history:{" "}
            {imported.balances.length} not collected
            {imported.added ? `, ${imported.added} new on this list` : ", all already on this list"}
            {imported.updated ? `, ${imported.updated} with a new status` : ""}.
          </p>
        )}
        {importProblem && <Problem text={importProblem} />}
      </div>
      <div className="filters" aria-label="Show balances that are">
        {FILTERS.map((f) => (
          <button key={f || "all"} className={filter === f ? "filter on" : "filter"} onClick={() => setFilter(f)}>
            {f ? BALANCE_WORDS[f] : "All"}
          </button>
        ))}
      </div>
      {problem && <Problem text={`We could not load the balances. ${problem}`} onRetry={() => setAttempt((a) => a + 1)} />}
      {!rows && !problem && <Loading text="Loading the balances." />}
      {rows && rows.length === 0 && (
        <Empty
          title={filter ? `No balances are "${BALANCE_WORDS[filter].toLowerCase()}" right now.` : "There are no dormant balances in the list."}
          text={filter ? "Choose All to see every balance." : `When ${business} adds its report, the balances appear here.`}
        />
      )}
      {rows && rows.length > 0 && (
        <>
          <p className="muted table-caption">
            {count(rows.length)} {rows.length === 1 ? "balance" : "balances"}, {money(total)} in all.
          </p>
          <table className="table">
            <thead>
              <tr>
                <th>Owner</th>
                <th>Last email</th>
                <th>Last place</th>
                <th>Last active</th>
                <th className="num">Amount</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => {
                const sc = notices[b.id];
                const sentOn = b.sent_to_state_on;
                return (
                  <Fragment key={b.id}>
                    <tr>
                      <td>
                        <span className="strong">{b.owner_display}</span>
                        {b.paypal && (
                          <div className="from-paypal">
                            <span className="tag-paypal">From PayPal</span>
                            <span className="pid small">{b.paypal.item_id}</span>
                          </div>
                        )}
                      </td>
                      <td className="mono">{b.last_email_masked}</td>
                      <td>
                        {b.last_city}
                        <div className="cell-note nowrap">{b.last_country}</div>
                      </td>
                      <td className="nowrap">{shortDate(b.last_activity)}</td>
                      <td className="num strong">{money(b.amount, b.currency)}</td>
                      <td>
                        <div className="status-cell">
                          {b.status === "ESCHEATED" ? (
                            <span className="state-sent">{sentOn ? `Sent to the state on ${longDate(sentOn)}` : "Sent to the state"}</span>
                          ) : b.paypal?.words && b.status === "DORMANT" ? (
                            <span className="paypal-words">{b.paypal.words}</span>
                          ) : (
                            <Chip tone={STATUS_TONE[b.status] ?? "neutral"}>{BALANCE_WORDS[b.status] ?? b.status}</Chip>
                          )}
                          {b.status === "DORMANT" && !sc && (
                            <button className="button-small" onClick={() => draft(b.id)}>
                              Draft notice
                            </button>
                          )}
                          {sc === "busy" && <span className="muted">Drafting...</span>}
                        </div>
                      </td>
                    </tr>
                    {sc && sc !== "busy" && (
                      <tr className="detail-row">
                        <td colSpan={6}>
                          {"error" in sc ? (
                            <Problem text={sc.error} onRetry={() => draft(b.id)} />
                          ) : (
                            <NoticeDraft n={sc} onClose={() => setNotices((x) => {
                              const next = { ...x };
                              delete next[b.id];
                              return next;
                            })} />
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

function Approvals({ claims, problem, reload, onChange }: {
  claims: Claim[] | null;
  problem: string;
  reload: () => void;
  onChange: (c: Claim[]) => void;
}) {
  const [approver, setApprover] = useState("Claims desk");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [done, setDone] = useState<Claim[]>([]);

  if (problem) return <Problem text={`We could not load the claims. ${problem}`} onRetry={reload} />;
  if (!claims) return <Loading text="Loading the claims." />;

  const waiting = claims.filter(waitingForPerson);

  const needsReason = (c: Claim) => c.proof.review?.required ?? c.proof.checks.some((k) => k.result !== "MATCH");
  const approve = async (c: Claim) => {
    if (!approver.trim()) {
      setErrors((e) => ({ ...e, [c.id]: "Please write your name, so the approval is on record." }));
      return;
    }
    const reason = (reasons[c.id] ?? "").trim();
    if (needsReason(c) && !reason) {
      setErrors((e) => ({ ...e, [c.id]: "Please write why you are satisfied, so the reason is on record." }));
      return;
    }
    setBusy(c.id);
    setErrors((e) => ({ ...e, [c.id]: "" }));
    try {
      const next = await approveClaim(c.id, approver.trim(), reason);
      onChange(claims.map((x) => (x.id === next.id ? next : x)));
      setDone((d) => [next, ...d]);
    } catch (e) {
      setErrors((x) => ({ ...x, [c.id]: `The approval did not go through. ${(e as Error).message}` }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section>
      {done.map((c) => (
        <div className="success" key={c.id} role="status">
          Approved by {c.proof.approved_by}. {c.claimant.name || "The owner"} can now send {money(c.amount!, c.currency)} home.
          {c.proof.review?.reason && <span className="success-reason"> Reason on record: {c.proof.review.reason}</span>}
        </div>
      ))}
      {waiting.length === 0 ? (
        <Empty title="Nothing is waiting for you." text="When documents do not fully match, the claim comes here for a person to decide." />
      ) : (
        <>
          {waiting.map((c) => {
            const must = needsReason(c);
            const reason = reasons[c.id] ?? "";
            const blocked = busy !== null || !approver.trim() || (must && !reason.trim());
            return (
            <article className="card review" key={c.id}>
              <div className="review-head">
                <div>
                  <p className="label">Claim by</p>
                  <p className="review-name">{c.claimant.name || "Name not given"}</p>
                  <p className="muted">
                    {[
                      c.claimant.former_names?.length ? `Says they were once ${c.claimant.former_names.join(", ")}` : "",
                      c.claimant.email,
                      c.claimant.country,
                    ].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <div className="review-amount">
                  <p className="label">Balance</p>
                  <p className="big-number">{money(c.amount!, c.currency)}</p>
                </div>
              </div>
              <div className="approve-row">
                <label className="field approver">
                  <span className="field-label">Approving as</span>
                  <input value={approver} onChange={(e) => setApprover(e.target.value)} />
                </label>
                <label className="field">
                  <span className="field-label">
                    Why you are satisfied{" "}
                    <span className="muted">{must ? "(required: not every check is a match)" : "(optional)"}</span>
                  </span>
                  <textarea
                    rows={2}
                    value={reason}
                    onChange={(e) => setReasons((r) => ({ ...r, [c.id]: e.target.value }))}
                    placeholder="Say which documents satisfied you, and why."
                  />
                </label>
                <button className="button" disabled={blocked} onClick={() => approve(c)}>
                  {busy === c.id ? "Approving..." : "Approve"}
                </button>
              </div>
              {errors[c.id] && <Problem text={errors[c.id]} />}
              {c.proof.documents && c.proof.documents.length > 0 && (
                <div>
                  <p className="label">What was read</p>
                  <DocumentsRead docs={c.proof.documents} compact business />
                </div>
              )}
              <ChecksTable checks={c.proof.checks} />
              {(c.proof.flags.length > 0 || c.proof.missing.length > 0) && (
                <div className="two-col">
                  {c.proof.flags.length > 0 && (
                    <div>
                      <p className="label">Warnings</p>
                      <PlainList items={c.proof.flags} tone="bad" />
                    </div>
                  )}
                  {c.proof.missing.length > 0 && (
                    <div>
                      <p className="label">Still missing</p>
                      <PlainList items={c.proof.missing} tone="warn" />
                    </div>
                  )}
                </div>
              )}
              <p className="muted">Approve only if you are satisfied this is the owner. Nothing is paid until you do, and your reason is shown to the owner.</p>
            </article>
            );
          })}
        </>
      )}
      <TurnedDown claims={claims.filter((c) => c.status === "REJECTED")} />
    </section>
  );
}

/** Claims the checks turned down. The claimant was told only that it could not be confirmed; the business sees why. */
function TurnedDown({ claims }: { claims: Claim[] }) {
  if (!claims.length) return null;
  return (
    <section className="turned-down">
      <h2 className="section-title">Turned down by the checks ({claims.length})</h2>
      <p className="muted table-caption">
        Each claimant was told only that the claim could not be confirmed. Nothing here can be approved or paid.
      </p>
      <div className="cards">
        {claims.map((c) => (
          <article className="card review" key={c.id}>
            <div className="review-head">
              <div>
                <p className="label">Claim by</p>
                <p className="review-name">{c.claimant.name || "Name not given"}</p>
                <p className="muted">
                  {[c.claimant.email, c.claimant.country, `${c.proof.attempts_left ?? 0} attempts left`].filter(Boolean).join(" · ")}
                </p>
              </div>
              <div className="review-amount">
                <p className="label">Balance</p>
                <p className="big-number">{money(c.amount!, c.currency)}</p>
              </div>
            </div>
            {c.proof.flags.length > 0 && (
              <div>
                <p className="label">Why it was turned down</p>
                <PlainList items={c.proof.flags} tone="bad" />
              </div>
            )}
            {c.proof.documents && c.proof.documents.length > 0 && (
              <div className="turned-docs">
                <p className="label">What was read</p>
                <DocumentsRead docs={c.proof.documents} compact business />
              </div>
            )}
            <ChecksTable checks={c.proof.checks} />
          </article>
        ))}
      </div>
    </section>
  );
}

function Invoices({ claims, problem, reload, business }: { claims: Claim[] | null; problem: string; reload: () => void; business: string }) {
  if (problem) return <Problem text={`We could not load the invoices. ${problem}`} onRetry={reload} />;
  if (!claims) return <Loading text="Loading the invoices." />;
  const billed = claims.filter((c) => c.invoice);
  if (!billed.length) return <Empty title="No invoices yet." text="An invoice is made for each owner who is paid home." />;
  return (
    <section>
      <p className="muted table-caption">One PayPal invoice from Homing for each owner paid home. {business} pays it, never the owner.</p>
      <table className="table">
        <thead>
          <tr>
            <th>Invoice</th>
            <th>Owner paid</th>
            <th className="num">Paid home</th>
            <th className="num">Fee</th>
            <th>Status</th>
            <th aria-label="Link" />
          </tr>
        </thead>
        <tbody>
          {billed.map((c) => (
            <tr key={c.id}>
              <td className="strong mono">{c.invoice!.number}</td>
              <td>{c.claimant.name}</td>
              <td className="num">{money(c.amount!, c.currency)}</td>
              <td className="num strong">{money(c.invoice!.amount)}</td>
              <td>
                <Chip tone={c.invoice!.status === "PAID" ? "good" : "accent"}>{sentence(c.invoice!.status.toLowerCase()).replace(/\.$/, "")}</Chip>
              </td>
              <td className="action-cell">
                <a className="button-small" href={c.invoice!.payer_url} target="_blank" rel="noreferrer">
                  Open in PayPal
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Results() {
  const [board, setBoard] = useState<Scoreboard | null>(null);
  const [problem, setProblem] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    setProblem("");
    getScoreboard().then(setBoard).catch((e: Error) => setProblem(e.message));
  }, [attempt]);
  if (problem) return <Problem text={`We could not load the results. ${problem}`} onRetry={() => setAttempt((a) => a + 1)} />;
  if (!board) return <Loading text="Adding up the results." />;
  const big: [string, string][] = [
    ["Paid home", money(board.paid_total)],
    ["Fees invoiced", money(board.fees_invoiced)],
  ];
  const small: [string, string][] = [
    ["Owners paid", count(board.paid)],
    ["Owners found", count(board.found)],
    ["Checked or approved", count(board.proven)],
    ["Not collected", count(board.unclaimed)],
    ["Returned by PayPal", count(board.returned)],
    ["Turned down", count(board.rejected)],
  ];
  return (
    <section>
      <div className="tally-big">
        {big.map(([k, v]) => (
          <div key={k} className="tally">
            <p className="tally-value">{v}</p>
            <p className="tally-label">{k}</p>
          </div>
        ))}
      </div>
      <div className="tally-small">
        {small.map(([k, v]) => (
          <div key={k} className="tally">
            <p className="tally-value">{v}</p>
            <p className="tally-label">{k}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

/** The model's draft of the business's letter to an owner, in the owner's likely language. Nothing is sent. */
function NoticeDraft({ n, onClose }: { n: Notice; onClose: () => void }) {
  const english = isEnglish(n.language);
  return (
    <div className="notice">
      <div className="notice-head">
        <p className="explain-label">
          {n.source === "SAVED" ? "Drafted earlier by the model (saved copy). Nothing is sent." : "Drafted by the model. Nothing is sent."}
        </p>
        <button className="button-quiet" onClick={onClose}>
          Close
        </button>
      </div>
      <div className={english ? "notice-cols notice-one" : "notice-cols"}>
        <div lang={n.language}>
          {!english && <p className="explain-lang">In {languageName(n.language)}</p>}
          <p className="notice-subject">{n.subject}</p>
          <p className="notice-body">{n.body}</p>
        </div>
        {!english && n.english && (
          <div lang="en">
            <p className="explain-lang">The same notice in English</p>
            {n.english_subject && <p className="notice-subject">{n.english_subject}</p>}
            <p className="notice-body">{n.english}</p>
          </div>
        )}
      </div>
    </div>
  );
}
