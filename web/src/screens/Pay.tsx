import { Fragment, useEffect, useState, type FormEvent } from "react";
import { getClaim, getPayPalRecord, newContact, payClaim, refreshClaim } from "../api";
import type { Claim, PayPalRecord, TimelineEvent } from "../types";
import { canPay, cleanReceiver, clock, money, PAYOUT_WORDS, receiverType, sentence, validReceiver } from "../format";
import { FALLBACK_META, Loading, Page, Problem, useMeta } from "../ui";

const isHeld = (ev: TimelineEvent) => ev.state === "HELD" || /^held by paypal/i.test(ev.title ?? "");

/** How PayPal's final answer reached Homing: a verified webhook delivery, or Homing asking PayPal. */
function confirmedLine(by: string | null | undefined): string | null {
  if (by === "WEBHOOK") return "Confirmed by PayPal webhook";
  if (by === "POLLING") return "Confirmed by asking PayPal";
  return null;
}

/** The server writes "Confirmed by PayPal webhook." into the note as well; the entry shows it as a tag instead. */
const noteWithoutHow = (note: string) => note.replace(/\s*Confirmed by PayPal webhook\.?/i, "").trim();

/** A note that repeats its entry's title ("Held by PayPal. The first payment ...") drops the repeat. */
const noteFor = (ev: TimelineEvent) => {
  const note = noteWithoutHow(ev.note);
  const t = (ev.title ?? "").trim();
  return t && note.toLowerCase().startsWith(`${t.toLowerCase()}.`) ? note.slice(t.length + 1).trim() : note;
};

const CURRENCY_NAMES: Record<string, string> = {
  USD: "US dollars",
  EUR: "euros",
  GBP: "British pounds",
  CAD: "Canadian dollars",
};

interface Props {
  claimId: string;
  onProof: () => void;
  onDone: () => void;
}

function ContactForm({ initial, button, busy, onSubmit, currency, label, wide }: {
  initial: string;
  button: string;
  busy: boolean;
  currency?: string;
  label: string;
  wide?: boolean;
  onSubmit: (receiver: string) => void;
}) {
  const meta = useMeta();
  const [receiver, setReceiver] = useState(initial);
  const isTest = meta.test_receivers.some((t) => t.toLowerCase() === receiver.trim().toLowerCase());
  const [hint, setHint] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!validReceiver(receiver)) {
      setHint("Please give an email address, or a phone number starting with your country code, like +1 416 555 0182.");
      return;
    }
    setHint("");
    onSubmit(cleanReceiver(receiver));
  };
  return (
    <form className={wide ? "form form-wide-card card" : "form card"} onSubmit={submit} noValidate>
      <label className="field">
        <span className="field-label">{label}</span>
        <input value={receiver} onChange={(e) => setReceiver(e.target.value)} placeholder="name@example.com or +1 416 555 0182" />
        {isTest && (
          <span className="test-chip" title="An account in PayPal's test system, so no real money moves">
            PayPal test account
          </span>
        )}
        <span className="muted field-hint">If you do not have PayPal yet, PayPal will invite you to collect it.</span>
      </label>
      {currency && (
        <p className="muted field-hint">
          Paid in {CURRENCY_NAMES[currency] ?? currency} ({currency}), the currency the balance is held in.
        </p>
      )}
      {hint && <Problem text={hint} />}
      <div className="form-action">
        <button className="button" type="submit" disabled={busy}>
          {busy ? "Sending..." : button}
        </button>
      </div>
    </form>
  );
}

export default function Pay({ claimId, onProof, onDone }: Props) {
  const meta = useMeta();
  const holder = meta.holder.name === FALLBACK_META.holder.name ? "the business" : meta.holder.name;
  const Holder = holder === "the business" ? "The business" : holder;
  const [changing, setChanging] = useState(false);
  const [record, setRecord] = useState<PayPalRecord | "busy" | null>(null);
  const [recordProblem, setRecordProblem] = useState("");
  const seeRecord = async () => {
    setRecord("busy");
    setRecordProblem("");
    try {
      setRecord(await getPayPalRecord(claimId));
    } catch (e) {
      setRecord(null);
      setRecordProblem(`PayPal's record could not be fetched. ${(e as Error).message}`);
    }
  };
  const [claim, setClaim] = useState<Claim | null>(null);
  const [problem, setProblem] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [sendProblem, setSendProblem] = useState("");
  const [pollProblem, setPollProblem] = useState("");

  useEffect(() => {
    setProblem("");
    getClaim(claimId).then(setClaim).catch((e: Error) => setProblem(e.message));
  }, [claimId, attempt]);

  // Ask PayPal every three seconds while the payment is out. Nothing is shown as paid until PayPal says so.
  const sent = claim?.payout.state === "SENT";
  useEffect(() => {
    if (!sent) return;
    let stop = false;
    const tick = async () => {
      try {
        const next = await refreshClaim(claimId);
        if (!stop) {
          setClaim(next);
          setPollProblem("");
        }
      } catch (e) {
        if (!stop) setPollProblem(`We could not reach PayPal for an update. We will keep trying. ${(e as Error).message}`);
      }
    };
    const t = setInterval(tick, 3000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [sent, claimId]);

  const send = async (receiver: string, again: boolean) => {
    setBusy(true);
    setSendProblem("");
    try {
      const next = again
        ? await newContact(claimId, receiver, receiverType(receiver))
        : await payClaim(claimId, receiver, receiverType(receiver), claim!.currency);
      setClaim(next);
      setChanging(false);
    } catch (e) {
      const message = (e as Error).message;
      setSendProblem(again || message.startsWith("Held by PayPal") ? message : `The payment was not sent. ${message}`);
      if (again) setChanging(false);
      // The server may have noted what PayPal said (for example a refused cancel); show it in the timeline.
      getClaim(claimId).then(setClaim).catch(() => undefined);
    } finally {
      setBusy(false);
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

  const amount = money(claim.amount!, claim.currency); // payable claims always carry their amount
  const p = claim.payout;

  // A turned-down claim has no payment page: it goes straight back to its result.
  if (!canPay(claim.proof, claim.status)) return <Redirect to={onProof} />;

  if (p.state === "NONE")
    return (
      <Page narrow>
        <div className="screen-head screen-head-form">
          <p className="eyebrow">Checked and ready</p>
          <h1 className="title">Where should we send your {amount}?</h1>
          <p className="lead">It goes straight to your PayPal account, wherever you live now.</p>
        </div>
        {claim.paypal_held && (
          <p className="muted field-hint">{`This balance is an earlier PayPal payout from ${holder}: ${claim.paypal_held}.`}</p>
        )}
        <ContactForm
          initial={claim.claimant.email}
          label="Your PayPal email or phone number"
          button="Send it home"
          busy={busy}
          currency={claim.currency}
          onSubmit={(r) => send(r, false)}
        />
        {sendProblem &&
          (sendProblem.startsWith("Held by PayPal") ? (
            <div className="held" role="status">
              {sendProblem}
            </div>
          ) : (
            <Problem text={sendProblem} />
          ))}
      </Page>
    );

  const timeline = p.timeline;
  const confirmed = p.state === "SENT" ? null : confirmedLine(p.confirmed_by);
  const lastHeld = [...p.timeline].reverse().find(isHeld);
  const held = p.state === "UNCLAIMED" && (Boolean(lastHeld) || sendProblem.startsWith("Held by PayPal"));
  const redProblem = sendProblem && !sendProblem.startsWith("Held by PayPal") ? sendProblem : "";

  const head =
    p.state === "CLAIMED"
      ? { tone: "good", title: `Paid. ${amount} is in your PayPal account.`, text: "PayPal has confirmed that the money was collected. This claim is closed." }
      : p.state === "SENT"
        ? { tone: "wait", title: `${amount} is on its way.`, text: `Sent to ${p.receiver_masked ?? "you"}. We are waiting for PayPal to confirm it was collected.` }
        : p.state === "UNCLAIMED"
          ? held
            ? { tone: "warn", title: "Held by PayPal.", text: `The payment sent to ${p.receiver_masked || "that contact"} has not been collected, so it will not be sent to another address. PayPal returns uncollected payments to the sender after 30 days.` }
            : { tone: "warn", title: "Not collected yet.", text: `PayPal is holding the payment sent to ${p.receiver_masked || "that contact"} until it is collected. If that address is no longer yours, you can give another one. It will never be paid twice.` }
          : p.state === "RETURNED"
          ? { tone: "warn", title: "The payment came back. Try another email or phone.", text: `Nobody collected the payment sent to ${p.receiver_masked || "that contact"}, so PayPal returned it. Nothing was paid, and it will never be paid twice.` }
          : p.state === "FAILED"
            ? { tone: "bad", title: "PayPal could not send this payment.", text: `No money has left. Please try again later, or contact ${holder}.` }
            : { tone: "warn", title: "This payment was cancelled.", text: `No money was paid. ${Holder} can send it again.` };

  return (
    <Page>
      <div className={`verdict verdict-${head.tone}`}>
        <div>
          <h1 className="title">{head.title}</h1>
          <p className="lead">{head.text}</p>
        </div>
        {p.state === "SENT" && <span className="spinner spinner-large" aria-label="Waiting for PayPal" />}
        {p.state !== "SENT" && confirmed && <span className="confirm-chip">{confirmed}</span>}
      </div>
      {pollProblem && <Problem text={pollProblem} />}

      {(p.state === "UNCLAIMED" || p.state === "RETURNED") && !held && (
        <section className="section-tight">
          {changing ? (
            <ContactForm initial="" wide label="A different email or phone number to send it to" button="Send to this address" busy={busy} onSubmit={(r) => send(r, true)} />
          ) : (
            <button className="button-secondary" onClick={() => setChanging(true)}>
              Use a different address
            </button>
          )}
          {redProblem && <Problem text={redProblem} />}
        </section>
      )}
      {held && redProblem && <Problem text={redProblem} />}

      <section className="section-tight pay-grid">
        <div>
          <h2 className="section-title">What PayPal has told us</h2>
          <ol className="timeline">
            {timeline.map((ev, i) => (
              <li key={i} className={`tl tl-${ev.state.toLowerCase()}`}>
                <span className="tl-dot" aria-hidden="true" />
                <div>
                  <p className="tl-state">{ev.title || PAYOUT_WORDS[ev.state] || sentence(ev.state.toLowerCase())}</p>
                  {noteFor(ev) && <p>{sentence(noteFor(ev))}</p>}
                  <p className="muted small">
                    {clock(ev.at)}
                    {ev.heard_by === "WEBHOOK" && " · PayPal webhook"}
                    {ev.heard_by === "POLLING" && " · Homing asked PayPal"}
                    {ev.paypal_id && (
                      <>
                        {" "}· PayPal id <span className="pid">{ev.paypal_id}</span>
                      </>
                    )}
                  </p>
                </div>
              </li>
            ))}
            {p.state === "SENT" && (
              <li className="tl tl-pending">
                <span className="tl-dot" aria-hidden="true" />
                <div>
                  <p className="tl-state">Collected</p>
                  <p className="muted">Not yet. This updates by itself.</p>
                </div>
              </li>
            )}
          </ol>
        </div>
        <div className="card facts">
          <p className="label">Payment</p>
          <dl>
            <dt>Amount</dt>
            <dd>{amount}</dd>
            <dt>Sent to</dt>
            <dd className="wrap-any">{p.receiver_masked || "Not sent"}</dd>
            <dt>PayPal batch</dt>
            <dd className={p.batch_id ? "pid" : "muted"}>{p.batch_id ?? "Not given yet"}</dd>
            <dt>PayPal item</dt>
            <dd className={p.item_id ? "pid" : "muted"}>{p.item_id ?? "Not given yet"}</dd>
            {confirmed && (
              <>
                <dt>Confirmed</dt>
                <dd>{confirmed.replace(/^Confirmed /, "")}</dd>
              </>
            )}
            <dt>Your cost</dt>
            <dd>Nothing. {Holder} pays the fee.</dd>
          </dl>
        </div>
      </section>

      {p.state === "CLAIMED" && record && record !== "busy" && (
        <section className="card paypal-record" aria-live="polite">
          <p className="paypal-record-head">This is PayPal's answer, fetched just now</p>
          <p className="muted small">
            <span className="pid">{record.endpoint}</span> · {clock(record.fetched_at)}
          </p>
          <dl>
            {(
              [
                ["transaction_status", record.record.transaction_status],
                ["payout_item_id", record.record.payout_item_id],
                ["payout_batch_id", record.record.payout_batch_id],
                ["transaction_id", record.record.transaction_id],
                ["time_processed", record.record.time_processed],
                ["amount", record.record.amount ? `${record.record.amount.value} ${record.record.amount.currency}` : null],
                ["payout_item_fee", record.record.payout_item_fee ? `${record.record.payout_item_fee.value} ${record.record.payout_item_fee.currency}` : null],
              ] as [string, string | null][]
            ).map(([k, v]) => (
              <Fragment key={k}>
                <dt>{k}</dt>
                <dd>{v ?? "not given"}</dd>
              </Fragment>
            ))}
          </dl>
        </section>
      )}
      {recordProblem && <Problem text={recordProblem} />}

      {p.state === "CLAIMED" && (
        <div className="form-action">
          <button className="button" onClick={seeRecord} disabled={record === "busy"}>
            {record === "busy" ? "Asking PayPal..." : record ? "Ask PayPal again" : "See PayPal's record"}
          </button>
          <button className="button-secondary" onClick={onDone}>
            Back to the start
          </button>
        </div>
      )}
    </Page>
  );
}

function Redirect({ to }: { to: () => void }) {
  useEffect(() => {
    to();
  }, [to]);
  return null;
}
