import { useState, type FormEvent } from "react";
import { createClaim, prepareStateClaim, search } from "../api";
import type { Match, Place, StateClaim } from "../types";
import { languageFor, longDate, sentence, strengthOf, toYou } from "../format";
import { Empty, FALLBACK_META, Loading, Page, Problem, Strength, useMeta } from "../ui";

export interface FindForm {
  name: string;
  formerNames: string[];
  emails: string[];
  places: Place[];
}

export const emptyForm = (): FindForm => ({ name: "", formerNames: [""], emails: [""], places: [{ city: "", country: "" }] });

interface Props {
  form: FindForm;
  setForm: (f: FindForm) => void;
  matches: Match[] | null;
  setMatches: (m: Match[] | null) => void;
  onClaimed: (claimId: string) => void;
}

export default function Find({ form, setForm, matches, setMatches, onClaimed }: Props) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [claiming, setClaiming] = useState<string | null>(null);
  const [claimProblem, setClaimProblem] = useState<{ id: string; text: string } | null>(null);
  const [packets, setPackets] = useState<Record<string, StateClaim | "busy">>({});
  const meta = useMeta();
  const named = meta.holder.name !== FALLBACK_META.holder.name;

  const packet = async (m: Match) => {
    setPackets((p) => ({ ...p, [m.balance_id]: "busy" }));
    setClaimProblem(null);
    try {
      const r = await prepareStateClaim(m.balance_id);
      setPackets((p) => ({ ...p, [m.balance_id]: r }));
    } catch (err) {
      setPackets((p) => {
        const next = { ...p };
        delete next[m.balance_id];
        return next;
      });
      setClaimProblem({ id: m.balance_id, text: `The claim packet could not be prepared. ${(err as Error).message}` });
    }
  };

  const filled = (xs: string[]) => xs.map((x) => x.trim()).filter(Boolean);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!form.name.trim()) {
      setProblem("Please tell us your name so we can look.");
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      const res = await search({
        name: form.name.trim(),
        former_names: filled(form.formerNames),
        emails: filled(form.emails),
        places: form.places.map((p) => ({ city: p.city.trim(), country: p.country.trim() })).filter((p) => p.city || p.country),
      });
      setMatches([...res.matches].sort((a, b) => strengthOf(b).value - strengthOf(a).value));
    } catch (err) {
      setProblem(`We could not search just now. ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const claim = async (m: Match) => {
    setClaiming(m.balance_id);
    setClaimProblem(null);
    try {
      const firstPlace = form.places.find((p) => p.country.trim());
      const c = await createClaim(m.balance_id, {
        name: form.name.trim(),
        email: filled(form.emails)[0] ?? "",
        country: firstPlace?.country.trim() ?? "",
        language: languageFor(firstPlace?.country ?? "", navigator.language || "en"),
        former_names: filled(form.formerNames),
      });
      onClaimed(c.id);
    } catch (err) {
      setClaimProblem({ id: m.balance_id, text: `We could not start your claim. ${(err as Error).message}` });
    } finally {
      setClaiming(null);
    }
  };

  if (busy) {
    return (
      <Page>
        <div className="center-block">
          <Loading text="Looking through the balances for your names, emails and places." />
        </div>
      </Page>
    );
  }

  if (matches) {
    const likely = matches.filter((m) => strengthOf(m).band !== "UNLIKELY").length;
    const sameName = matches.some((m) => m.doubts.some((d) => /carries the same name|has this name/i.test(d)));
    return (
      <Page>
        <div className="screen-head">
          <h1 className="title">
            {matches.length === 0
              ? "No balance matches you yet."
              : likely > 0
                ? `We found ${likely === 1 ? "a balance" : `${likely} balances`} that could be yours.`
                : "We found nothing that looks like yours."}
          </h1>
          <p className="lead">
            {matches.length === 0
              ? "Try adding a former name, an older email or another place you lived. Small details often make the difference."
              : sameName
                ? "More than one person on the list has this name, so we cannot be sure which is yours. An old email or street you used would tell them apart."
              : matches.length === 1
                ? "It shows how strong the match is and why. If it is yours, say so, and check your documents next. The exact amount shows once you open a claim."
                : "Each one shows how strong the match is and why. Choose the one that is yours. The exact amount shows once you open a claim."}
          </p>
          <button className="button-quiet" onClick={() => setMatches(null)}>
            Change my details
          </button>
        </div>
        {matches.length === 0 ? (
          <Empty title="Nothing found under these details." text="That does not mean there is nothing. Many old records use a former name." />
        ) : (
          <div className="cards">
            {matches.map((m) => (
              <article className={`card match ${strengthOf(m).band === "UNLIKELY" ? "match-faint" : ""}`} key={m.balance_id}>
                <div className="match-main">
                <div>
                  <p className="label">Owner on the record</p>
                  <p className="match-owner">{m.owner_display}</p>
                  {m.city && <p className="muted">Last known city: {m.city}</p>}
                </div>
                <Strength m={m} />
                <div className="match-why">
                  <div>
                    <p className="label">Why we think so</p>
                    {m.reasons.length ? (
                      <ul className="plain-list plain-good">{m.reasons.map((r, i) => <li key={i}>{toYou(r)}</li>)}</ul>
                    ) : (
                      <p className="muted">No clear reasons.</p>
                    )}
                  </div>
                  <div>
                    <p className="label">What does not fit</p>
                    {m.doubts.length ? (
                      <ul className="plain-list plain-warn">{m.doubts.map((r, i) => <li key={i}>{toYou(r)}</li>)}</ul>
                    ) : (
                      <p className="muted">Nothing we could see.</p>
                    )}
                  </div>
                </div>
                </div>
                <div className="match-side">
                  <p className="label">Amount</p>
                  <p className="match-band">{sentence(m.amount_band).replace(/\.$/, "")}</p>
                  {m.status === "ESCHEATED" ? (
                    <>
                      <p className="state-note">
                        {m.sent_to_state_on
                          ? `Sent to the State of California on ${longDate(m.sent_to_state_on)}`
                          : "Already with the State of California"}
                      </p>
                      {!packets[m.balance_id] && (
                        <button className="button" onClick={() => packet(m)}>
                          Prepare claim packet
                        </button>
                      )}
                      {packets[m.balance_id] === "busy" && <p className="muted">Preparing your packet...</p>}
                    </>
                  ) : (
                    <button
                      className={strengthOf(m).band !== "UNLIKELY" ? "button" : "button-secondary"}
                      disabled={claiming !== null}
                      onClick={() => claim(m)}
                    >
                      {claiming === m.balance_id ? "Starting your claim..." : "This is mine"}
                    </button>
                  )}
                </div>
                {(() => {
                  const sc = packets[m.balance_id];
                  if (!sc || sc === "busy") return null;
                  return (
                    <div className="state-claim">
                      <div className="state-claim-head">
                        <p className="state-claim-title">
                          {meta.holder.name === FALLBACK_META.holder.name ? "The company" : meta.holder.name} handed this money to the State of California
                          {m.sent_to_state_on ? ` on ${longDate(m.sent_to_state_on)}` : ""}, so you claim it from the state. Your claim packet is ready for
                          you to review, sign and send. Homing has not filed anything.
                        </p>
                        <a className="button-small" href={sc.pdf_url} target="_blank" rel="noreferrer">
                          Open the claim packet (PDF)
                        </a>
                      </div>
                      <ol className="steps-list">
                        {sc.steps.map((st, i) => (
                          <li key={i}>{sentence(st)}</li>
                        ))}
                      </ol>
                    </div>
                  );
                })()}
                {claimProblem?.id === m.balance_id && (
                  <div className="match-problem">
                    <Problem text={claimProblem.text} />
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </Page>
    );
  }

  const setList = (key: "formerNames" | "emails", i: number, v: string) =>
    setForm({ ...form, [key]: form[key].map((x, j) => (j === i ? v : x)) });

  return (
    <Page narrow>
      <div className="screen-head screen-head-form">
        <h1 className="title">Tell us who you are, and who you used to be.</h1>
        <p className="lead">
          {named ? (
            <>
              {meta.holder.name}, the company holding your money, may have your payout under an old name, email or address. Give us any you
              have used.
            </>
          ) : (
            "The company holding your money has it under an old name, email or address, so give us any you have used."
          )}
        </p>
      </div>
      <form className="form form-grid card" onSubmit={submit} noValidate>
        <label className="field">
          <span className="field-label">Your full name</span>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="For example, Amara Okafor" autoComplete="name" />
        </label>

        <fieldset className="field">
          <legend className="field-label">
            Former names <span className="muted">(or other spellings)</span>
          </legend>
          {form.formerNames.map((v, i) => (
            <input key={i} value={v} onChange={(e) => setList("formerNames", i, e.target.value)} placeholder="For example, Amara Nwosu" />
          ))}
          <button type="button" className="button-link" onClick={() => setForm({ ...form, formerNames: [...form.formerNames, ""] })}>
            Add another name
          </button>
        </fieldset>

        <fieldset className="field">
          <legend className="field-label">
            Emails you have used <span className="muted">(old ones help)</span>
          </legend>
          {form.emails.map((v, i) => (
            <input key={i} type="email" value={v} onChange={(e) => setList("emails", i, e.target.value)} placeholder="name@example.com" />
          ))}
          <button type="button" className="button-link" onClick={() => setForm({ ...form, emails: [...form.emails, ""] })}>
            Add another email
          </button>
        </fieldset>

        <fieldset className="field">
          <legend className="field-label">Places you have lived</legend>
          {form.places.map((p, i) => (
            <div className="pair" key={i}>
              <input
                value={p.city}
                placeholder="City"
                aria-label="City"
                onChange={(e) => setForm({ ...form, places: form.places.map((x, j) => (j === i ? { ...x, city: e.target.value } : x)) })}
              />
              <input
                value={p.country}
                placeholder="Country"
                aria-label="Country"
                onChange={(e) => setForm({ ...form, places: form.places.map((x, j) => (j === i ? { ...x, country: e.target.value } : x)) })}
              />
            </div>
          ))}
          <button type="button" className="button-link" onClick={() => setForm({ ...form, places: [...form.places, { city: "", country: "" }] })}>
            Add another place
          </button>
        </fieldset>

        <div className="form-action form-wide">
          <button className="button" type="submit">
            Look for my money
          </button>
          {problem ? (
            <p className="inline-problem" role="alert">
              {problem}
            </p>
          ) : (
            <p className="muted">We only use these details to find your payout.</p>
          )}
        </div>
      </form>
    </Page>
  );
}
