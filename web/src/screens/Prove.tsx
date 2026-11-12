import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { getClaim, leavePayoutAddress, uploadDocuments } from "../api";
import type { Claim } from "../types";
import { canPay, longDate, money } from "../format";
import { ChecksTable, DocumentsRead, ExplanationCard, FALLBACK_META, Loading, Page, PlainList, Problem, Thumb, useMeta } from "../ui";

interface Props {
  claimId: string;
  onPay: () => void;
  onBack: () => void;
}

const KINDS = ["A photo ID, like a passport or identity card", "An old statement, bill or lease with your old address", "Anything that links an old name to your name now"];

/** Pictures of the files the person chose, for as long as they are on screen. */
function usePreviews(files: File[]) {
  const urls = useMemo(() => files.map((f) => (f.type.startsWith("image/") ? URL.createObjectURL(f) : null)), [files]);
  useEffect(() => () => urls.forEach((u) => u && URL.revokeObjectURL(u)), [urls]);
  return urls;
}

export default function Prove({ claimId, onPay, onBack }: Props) {
  const meta = useMeta();
  const holder = meta.holder.name === FALLBACK_META.holder.name ? "the company holding your money" : meta.holder.name;
  const [claim, setClaim] = useState<Claim | null>(null);
  const [problem, setProblem] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [files, setFiles] = useState<File[]>([]);
  const [reading, setReading] = useState(false);
  const [uploadProblem, setUploadProblem] = useState("");
  const [addingMore, setAddingMore] = useState(false);
  const [over, setOver] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const previews = usePreviews(files);

  useEffect(() => {
    setProblem("");
    getClaim(claimId).then(setClaim).catch((e: Error) => setProblem(e.message));
  }, [claimId, attempt]);

  // While a person at the business has to decide, look again every few seconds so the approval shows up here.
  const waiting = claim?.proof.verdict === "NEEDS_REVIEW" && !claim.proof.approved_by && !addingMore;
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(() => {
      getClaim(claimId).then(setClaim).catch(() => undefined);
    }, 4000);
    return () => clearInterval(t);
  }, [waiting, claimId]);

  const add = (list: FileList | null) => {
    if (!list) return;
    const incoming = Array.from(list);
    setFiles((prev) => [...prev, ...incoming.filter((f) => !prev.some((p) => p.name === f.name && p.size === f.size))]);
  };

  const drop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    add(e.dataTransfer.files);
  };

  const read = async () => {
    setReading(true);
    setUploadProblem("");
    try {
      const next = await uploadDocuments(claimId, files);
      setClaim(next);
      setFiles([]);
      setAddingMore(false);
    } catch (e) {
      setUploadProblem(`We could not read your documents. ${(e as Error).message}`);
    } finally {
      setReading(false);
    }
  };

  if (problem)
    return (
      <Page>
        <Problem text={`We could not open your claim. ${problem}`} onRetry={() => setAttempt((a) => a + 1)} />
      </Page>
    );
  if (!claim)
    return (
      <Page>
        <div className="center-block">
          <Loading text="Opening your claim." />
        </div>
      </Page>
    );

  const proof = claim.proof;
  const rejected = proof.verdict === "REJECTED" || claim.status === "REJECTED";

  if (reading)
    return (
      <Page narrow>
        <div className="screen-head screen-head-form">
          {claim.amount != null && <p className="eyebrow">{money(claim.amount, claim.currency)} is waiting</p>}
          <h1 className="title title-reading">
            <span className="spinner spinner-inline" aria-hidden="true" />
            Reading the documents
          </h1>
          <p className="lead" role="status">
            The model reads what is printed on {files.length === 1 ? "your document" : `each of your ${files.length} documents`}. Plain code then
            compares it with the record and decides. The model can stop a claim or send it to a person, but it can never approve or pay one.
          </p>
        </div>
        <ul className="thumbs thumbs-reading">
          {files.map((f, i) => (
            <li key={f.name + f.size}>
              <Thumb src={previews[i]} label={f.name} />
              <span className="thumb-name">{f.name}</span>
              <span className="thumb-state">Reading</span>
            </li>
          ))}
        </ul>
      </Page>
    );

  const showUpload = proof.verdict === "NONE" || addingMore;

  if (showUpload)
    return (
      <Page narrow>
        <div className="screen-head screen-head-form">
          {claim.amount != null && <p className="eyebrow">{money(claim.amount, claim.currency)} is waiting</p>}
          <h1 className="title">Check your documents against the record.</h1>
          <p className="lead">Add photos or PDFs of what you still have. Nothing is paid until they match the account record.</p>
        </div>
        <div className={files.length ? "prove-grid prove-grid-full" : "prove-grid"}>
          <div
            className={`drop ${over ? "drop-over" : ""} ${files.length ? "drop-filled" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={drop}
            onClick={() => picker.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && picker.current?.click()}
          >
            <input ref={picker} type="file" multiple accept="image/*,application/pdf" hidden onChange={(e) => add(e.target.files)} />
            {files.length === 0 ? (
              <>
                <p className="drop-title">Drop your documents here</p>
                <p className="muted">or click to choose photos or PDFs</p>
              </>
            ) : (
              <>
                <ul className="thumbs">
                  {files.map((f, i) => (
                    <li key={f.name + f.size}>
                      <Thumb src={previews[i]} label={f.name} />
                      <span className="thumb-name">{f.name}</span>
                      <button
                        className="button-link"
                        onClick={(e) => {
                          e.stopPropagation();
                          setFiles(files.filter((x) => x !== f));
                        }}
                      >
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
                <p className="muted drop-more">Click or drop to add another</p>
              </>
            )}
          </div>
          {files.length === 0 && (
          <div className="card helps">
            <p className="label">What helps</p>
            <ul className="plain-list plain-good">
              {KINDS.map((k) => (
                <li key={k}>{k}</li>
              ))}
            </ul>
            {!rejected && proof.missing.length > 0 && (
              <>
                <p className="label label-gap">Still missing from before</p>
                <PlainList items={proof.missing} tone="warn" />
              </>
            )}
          </div>
          )}
        </div>
        {uploadProblem && <Problem text={uploadProblem} />}
        <div className="form-action form-action-tight">
          <button className="button" disabled={!files.length} onClick={read}>
            {files.length ? `Check ${files.length === 1 ? "my document" : `my ${files.length} documents`}` : "Add a document to continue"}
          </button>
          {addingMore ? (
            <button className="button-quiet" onClick={() => setAddingMore(false)}>
              Back to the result
            </button>
          ) : (
            <button className="button-quiet" onClick={onBack}>
              Not mine after all
            </button>
          )}
        </div>
      </Page>
    );

  const approvedByPerson = Boolean(proof.approved_by);
  const reason = proof.review?.reason?.trim();
  const payable = canPay(proof, claim.status);
  const left = proof.attempts_left;
  // A turned-down claim is told one fixed sentence and how many attempts are left, nothing else.
  const message = proof.message || `We could not confirm this claim. ${holder === "the company holding your money" ? "The business" : holder} has been told.`;
  const [first, ...rest] = message.split(/(?<=\.)\s+/);
  const verdict =
    rejected
      ? { tone: "bad", title: first, text: rest.join(" ") }
      : proof.verdict === "APPROVED"
        ? { tone: "good", title: "Checked. Your documents match the record.", text: "You can send it home now." }
        : approvedByPerson
          ? {
              tone: "good",
              title: `Approved by a person at ${holder}.`,
              text: reason ? `Their reason: “${reason}”` : `${proof.approved_by} checked your documents and said yes. You can send it home now.`,
            }
          : { tone: "warn", title: `A person at ${holder} must approve this.`, text: "Most of it matches, but not everything. Someone will look at it. This page updates by itself when they decide." };

  return (
    <Page>
      <div className={`verdict verdict-${verdict.tone}`}>
        <div>
          <h1 className="title">{verdict.title}</h1>
          <p className={approvedByPerson && reason && !rejected ? "lead reason-line" : "lead"}>{verdict.text}</p>
          {rejected && typeof left === "number" && (
            <p className="muted verdict-sub">{left > 0 ? `${left} ${left === 1 ? "attempt" : "attempts"} left.` : "No attempts left."}</p>
          )}
          {approvedByPerson && reason && !rejected && proof.verdict !== "APPROVED" && (
            <p className="muted verdict-sub">
              Approved by {proof.approved_by}
              {proof.review?.approved_at ? ` on ${longDate(proof.review.approved_at)}` : ""}. You can send it home now.
            </p>
          )}
        </div>
        <div className="verdict-side">
          {claim.amount != null && (
            <div>
              <p className="label">The balance</p>
              <p className="big-number">{money(claim.amount, claim.currency)}</p>
            </div>
          )}
          {payable ? (
            <button className="button" onClick={onPay}>
              Continue to payment
            </button>
          ) : rejected ? (
            typeof left === "number" ? (
              left > 0 ? (
                <button className="button-secondary" onClick={() => setAddingMore(true)}>
                  Try other documents
                </button>
              ) : (
                <p className="muted">No attempts left</p>
              )
            ) : null
          ) : (
            <button className="button-secondary" onClick={() => setAddingMore(true)}>
              Add another document
            </button>
          )}
        </div>
      </div>
      {waiting && <Loading text={`Waiting for a person at ${holder} to decide.`} />}
      {!rejected && proof.verdict === "NEEDS_REVIEW" && claim.status !== "PAYING" && claim.status !== "PAID" && (
        <AddressBox claim={claim} holder={holder} onSaved={setClaim} />
      )}

      {!rejected && proof.documents && proof.documents.length > 0 && (
        <section className="section-tight claimant-docs">
          <h2 className="section-title">What was read</h2>
          <DocumentsRead docs={proof.documents} />
        </section>
      )}

      {!rejected && (
        <section className="section-tight compared">
          <h2 className="section-title">What we compared</h2>
          <ChecksTable checks={proof.checks} />
        </section>
      )}

      {!rejected && proof.explanation?.text && (
        <section className="section-tight">
          <h2 className="section-title">The decision in words</h2>
          <ExplanationCard e={proof.explanation} />
        </section>
      )}

      {!rejected && (proof.flags.length > 0 || proof.missing.length > 0) && (
        <section className="section-tight two-col">
          {proof.flags.length > 0 && (
            <div className="card">
              <p className="label">Warnings</p>
              <PlainList items={proof.flags} tone="bad" />
            </div>
          )}
          {!rejected && proof.missing.length > 0 && (
            <div className="card">
              <p className="label">Still missing</p>
              <PlainList items={proof.missing} tone="warn" />
            </div>
          )}
        </section>
      )}
    </Page>
  );
}

function AddressBox({ claim, holder, onSaved }: { claim: Claim; holder: string; onSaved: (c: Claim) => void }) {
  const [value, setValue] = useState(claim.payout_address || "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const saved = claim.payout_address;
  const save = async () => {
    setBusy(true);
    setProblem("");
    try {
      onSaved(await leavePayoutAddress(claim.id, value.trim()));
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="section-tight card address-box">
      <label className="label" htmlFor="payout-address">If {holder} approves, send it to:</label>
      <div className="address-row">
        <input id="payout-address" className="input" type="email" placeholder="Your PayPal email" value={value}
          onChange={(e) => setValue(e.target.value)} />
        <button className="button-secondary" onClick={save} disabled={busy || !value.includes("@") || value.trim() === saved}>
          {busy ? "Saving..." : saved && value.trim() === saved ? "Saved" : "Save this address"}
        </button>
      </div>
      {saved && <p className="muted">Saved. Once a person approves, {holder} can pay {claim.amount != null ? money(claim.amount, claim.currency) : "the balance"} to this PayPal account without asking you again.</p>}
      {problem && <Problem text={problem} />}
    </section>
  );
}
