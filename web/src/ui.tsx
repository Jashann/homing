import { createContext, useContext, type ReactNode } from "react";
import type { Check, Explanation, Meta, ProofDocument } from "./types";
import { datesIn, fieldName, isDemoNote, isEnglish, languageName, longDate, RESULT_WORDS, sentence, strengthOf, withoutDemoNotes, withoutDemoWord } from "./format";

// What GET /api/meta says about this server: which business holds the money, and whether the data is sample data.
// Until it arrives (or if it cannot be read) the plain words below are used.
export const FALLBACK_META: Meta = {
  holder: { name: "the company holding your money", kind: "", sample: false },
  demo: false,
  test_receivers: [],
  model_available: true,
};
export const MetaContext = createContext<Meta>(FALLBACK_META);
export const useMeta = () => useContext(MetaContext);

export function DemoBanner({ isPublic }: { isPublic?: boolean }) {
  return (
    <div className="demo-banner" role="note">
      Demo: every person and document is invented, and sample-stamped documents are accepted. PayPal's test system, no real money.
      {isPublic && <span className="demo-banner-public">Public demo: payments go only to PayPal sandbox test accounts.</span>}
    </div>
  );
}

export type StepId = "money" | "find" | "prove" | "pay";
const STEPS: { id: StepId; label: string }[] = [
  { id: "money", label: "The money" },
  { id: "find", label: "Find yours" },
  { id: "prove", label: "Check your documents" },
  { id: "pay", label: "Send it home" },
];

interface TopBarProps {
  view: "owner" | "holder";
  step?: StepId;
  onStep?: Partial<Record<StepId, () => void>>;
  onView: (v: "owner" | "holder") => void;
  onHome: () => void;
}

export function TopBar({ view, step, onStep = {}, onView, onHome }: TopBarProps) {
  const meta = useMeta();
  const at = STEPS.findIndex((s) => s.id === step);
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <button className="wordmark" onClick={onHome} aria-label="Homing, back to the start">
          <span className="wordmark-dot" aria-hidden="true" />
          Homing
        </button>
        {view === "owner" ? (
          <nav aria-label="Your steps">
            <ol className="journey">
              {STEPS.map((s, i) => {
                const state = i === at ? "now" : i < at ? "done" : "later";
                const go = i < at ? onStep[s.id] : undefined;
                const inner = (
                  <>
                    <span className="journey-num">{i < at ? "✓" : i + 1}</span>
                    <span>{s.label}</span>
                  </>
                );
                return (
                  <li key={s.id} className={`journey-step ${state}`} aria-current={i === at ? "step" : undefined}>
                    {go ? <button onClick={go}>{inner}</button> : <span className="journey-static">{inner}</span>}
                  </li>
                );
              })}
            </ol>
          </nav>
        ) : (
          <p className="topbar-note">{meta.holder.name === FALLBACK_META.holder.name ? "The business's desk" : `${meta.holder.name}'s desk`}</p>
        )}
        <div className="switch" role="tablist" aria-label="Whose view">
          <button role="tab" aria-selected={view === "owner"} className={view === "owner" ? "on" : ""} onClick={() => onView("owner")}>
            I am owed money
          </button>
          <button role="tab" aria-selected={view === "holder"} className={view === "holder" ? "on" : ""} onClick={() => onView("holder")}>
            I hold the money
          </button>
        </div>
      </div>
    </header>
  );
}

export function Page({ children, wide, narrow }: { children: ReactNode; wide?: boolean; narrow?: boolean }) {
  return <main className={wide ? "page page-wide" : narrow ? "page page-narrow" : "page"}>{children}</main>;
}

export function Loading({ text }: { text: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden="true" />
      <p>{text}</p>
    </div>
  );
}

export function Problem({ text, onRetry }: { text: string; onRetry?: () => void }) {
  return (
    <div className="problem" role="alert">
      <p>{text}</p>
      {onRetry && (
        <button className="button-quiet" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ title, text }: { title: string; text?: string }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {text && <p className="muted">{text}</p>}
    </div>
  );
}

export function Chip({ tone, children }: { tone: "good" | "warn" | "bad" | "neutral" | "accent"; children: ReactNode }) {
  return <span className={`chip chip-${tone}`}>{children}</span>;
}

export function Strength({ m }: { m: { strength?: number; band?: string; probability: number } }) {
  const { words, tone, value } = strengthOf(m);
  return (
    <div className={`likely likely-${tone}`}>
      <div className="likely-head">
        <span className="likely-words">{words}</span>
        <span className="likely-pct">Match strength {value}/100</span>
      </div>
      <div className="likely-track" role="img" aria-label={`${words}, match strength ${value} out of 100`}>
        <div className="likely-fill" style={{ width: `${Math.max(value, 3)}%` }} />
      </div>
    </div>
  );
}

/** "identity card" becomes "Identity card"; emails and abbreviations such as "ul. Długa" stay as printed. */
const capital = (v: string) => (v.includes("@") || /^[a-z]{1,4}\.\s/.test(v) ? v : v.charAt(0).toUpperCase() + v.slice(1));

const RESULT_TONE = { MATCH: "good", PARTIAL: "warn", MISMATCH: "bad", MISSING: "neutral" } as const;

export function ChecksTable({ checks }: { checks: Check[] }) {
  if (!checks.length) return <Empty title="No checks yet." text="The checks appear here once the documents have been read." />;
  return (
    <table className="table checks">
      <thead>
        <tr>
          <th>What we checked</th>
          <th>
            The record says
            <span className="th-sub">Hidden to protect the owner</span>
          </th>
          <th>The document says</th>
          <th>Result</th>
        </tr>
      </thead>
      <tbody>
        {checks.map((c, i) => {
          const note = withoutDemoNotes(c.note || "");
          const value = c.document_value ? datesIn(c.document_value) : "";
          return (
          <tr key={`${c.field}-${i}`}>
            <td className="strong">{fieldName(c.field)}</td>
            <td className="mono">
              {c.record_value_masked ? datesIn(c.record_value_masked) : <span className="muted">{c.field === "document" ? "Checked on its own" : "Nothing on record"}</span>}
            </td>
            <td>
              {value ? capital(value) : <span className="muted">Not shown in any document</span>}
              {note && <div className="cell-note">{sentence(datesIn(note))}</div>}
            </td>
            <td>
              <Chip tone={RESULT_TONE[c.result] ?? "neutral"}>{RESULT_WORDS[c.result] ?? c.result}</Chip>
            </td>
          </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function PlainList({ items, tone }: { items: string[]; tone: "good" | "warn" | "bad" }) {
  return (
    <ul className={`plain-list plain-${tone}`}>
      {items.map((t, i) => (
        <li key={i}>{sentence(t)}</li>
      ))}
    </ul>
  );
}

/** A picture of a document: the server's thumbnail, or the file the person just chose. */
export function Thumb({ src, label }: { src?: string | null; label: string }) {
  return (
    <div className="thumb">
      {src ? <img src={src} alt={label} /> : <span className="thumb-blank">{label.split(".").pop()?.toUpperCase() || "FILE"}</span>}
    </div>
  );
}

function dateText(d: { label?: string; value?: string }): string {
  const v = d.value ? datesIn(d.value) : "";
  return d.label ? `${sentence(d.label).replace(/\.$/, "")}: ${v}` : v;
}

/** What the model read on each document, card by card: only what is printed, never an opinion.
 * The model's remarks show in red under "What looked wrong" only when it saw signs of editing. Its other remarks are
 * grey "Notes", and only the business sees them (`business`). */
export function DocumentsRead({ docs, compact, business }: { docs: ProofDocument[]; compact?: boolean; business?: boolean }) {
  if (!docs.length) return null;
  return (
    <div className={compact ? "docs-read docs-read-compact" : "docs-read"}>
      {docs.map((d, i) => {
        const r = d.read || {};
        const rows: [string, string][] = [];
        if (r.names?.length) rows.push([r.names.length > 1 ? "Names" : "Name", r.names.join(", ")]);
        if (r.date_of_birth) rows.push(["Date of birth", datesIn(r.date_of_birth)]);
        if (r.addresses?.length) rows.push([r.addresses.length > 1 ? "Addresses" : "Address", r.addresses.join("; ")]);
        if (r.emails?.length) rows.push([r.emails.length > 1 ? "Emails" : "Email", r.emails.join(", ")]);
        if (r.issuer) rows.push(["Issued by", r.issuer]);
        // The date of birth has its own row; the other dates keep their printed label ("Expires: 19 August 2030").
        const others = (r.dates ?? []).filter((x) => !/birth/i.test(x.label ?? ""));
        if (others.length) rows.push([others.length > 1 ? "Other dates" : "Other date", others.map(dateText).filter(Boolean).join("; ")]);
        const concerns = (d.concerns ?? []).filter((c) => !isDemoNote(c));
        const unread = d.status && d.status !== "read";
        return (
          <article className={`card doc-card ${d.signs_of_editing ? "doc-card-edited" : ""}`} key={`${d.file}-${i}`}>
            <Thumb src={d.thumb_url} label={d.file} />
            <div className="doc-body">
              <div className="doc-head">
                <div>
                  <p className="doc-kind">{withoutDemoWord(d.kind || "") || "Document"}</p>
                  <p className="doc-file">{d.file}</p>
                </div>
                {d.reading_source === "SAVED" && <span className="saved-note">Saved reading</span>}
              </div>
              {d.signs_of_editing && (
                <p className="edit-flag" role="alert">
                  Signs of editing. This document looks changed after it was made, so it cannot be used.
                </p>
              )}
              {unread ? (
                <p className="muted">{d.problem || "This document could not be read."}</p>
              ) : rows.length ? (
                <dl className="doc-fields">
                  {rows.map(([k, v]) => (
                    <div key={k}>
                      <dt>{k}</dt>
                      <dd>{v}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="muted">Nothing could be read on this document.</p>
              )}
              {concerns.length > 0 && d.signs_of_editing && (
                <div className="doc-concerns doc-concerns-bad">
                  <p className="doc-concerns-title">What looked wrong</p>
                  <PlainList items={concerns.map(datesIn)} tone="bad" />
                </div>
              )}
              {concerns.length > 0 && !d.signs_of_editing && business && (
                <div className="doc-concerns doc-notes">
                  <p className="doc-concerns-title">Notes</p>
                  <ul className="plain-list plain-notes">
                    {concerns.map((t, k) => (
                      <li key={k}>{sentence(datesIn(t))}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}

/** The model's account of a decision that plain code already made. */
export function ExplanationCard({ e }: { e: Explanation }) {
  const english = isEnglish(e.language);
  return (
    <div className="card explain">
      <p className="explain-label">
        {e.source === "PLAIN"
          ? "Written by Homing's code after the decision (no model was available)"
          : e.source === "SAVED"
            ? "Written earlier by the model for this sample case, after the decision; it cannot change the result"
            : "Written by the model after the decision; it cannot change the result"}
      </p>
      <p className="explain-text" lang={e.language}>
        {e.text}
      </p>
      {!english && e.english && (
        <div className="explain-english">
          <p className="explain-lang">In English (the text above is in {languageName(e.language)})</p>
          <p lang="en">{e.english}</p>
        </div>
      )}
    </div>
  );
}

export const when = (iso?: string | null) => (iso ? longDate(iso) : "");
