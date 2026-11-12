import { useEffect, useState } from "react";
import { getTotals } from "../api";
import type { Totals } from "../types";
import { count, longDate, money, shortMoney } from "../format";
import { Loading, Page, Problem } from "../ui";

export default function Opening({ onBegin }: { onBegin: () => void }) {
  const [totals, setTotals] = useState<Totals | null>(null);
  const [problem, setProblem] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setProblem("");
    getTotals().then(setTotals).catch((e: Error) => setProblem(e.message));
  }, [attempt]);

  const maxRecords = totals ? Math.max(...totals.bands.map((b) => b.records)) : 1;

  return (
    <Page>
      <section className="hero">
        <p className="tagline">Homing finds the owner of a forgotten payout, checks their documents against the account record, and pays it home through PayPal.</p>
        {!totals && !problem && <Loading text="Counting the money people never collected." />}
        {problem && <Problem text={`We could not load the numbers. ${problem}`} onRetry={() => setAttempt((a) => a + 1)} />}
        {totals && (
          <>
            <h1 className="hero-number">{money(totals.holder_total, "USD", false)}</h1>
            <p className="hero-line">
              in {count(totals.holder_records)} records that PayPal reported to California as unclaimed property
            </p>
            <p className="source">
              Source: California State Controller's Office, unclaimed property records, {longDate(totals.as_of)}.{" "}
              <a href={totals.source_url} target="_blank" rel="noreferrer">
                See the source
              </a>
            </p>
            <p className="honest">That money is already with the state. Homing is for the step before: the payouts a business still holds.</p>
          </>
        )}
        <div className="hero-action">
          <button className="button" onClick={onBegin}>
            Find mine
          </button>
          <p className="muted">It takes about two minutes, and it is free for you.</p>
        </div>
      </section>

      {totals && (
        <>
          <section className="section">
            <h2 className="section-title">How big the records are, by number of records</h2>
            <p className="muted section-lead">
              Most are small. But {count(totals.over_100.records)} are $100 or more, worth {shortMoney(totals.over_100.total)} together.
            </p>
            <div className="bands" role="list">
              {totals.bands.map((b) => (
                <div className="band" role="listitem" key={b.label} title={`${b.label}: ${count(b.records)} records, ${money(b.total, "USD", false)}`}>
                  <span className="band-label">{b.label}</span>
                  <div className="band-track">
                    <div className="band-fill" style={{ width: `${Math.max((b.records / maxRecords) * 100, 1.2)}%` }} />
                  </div>
                  <span className="band-value">
                    <strong>{count(b.records)}</strong>
                    <span className="muted"> records, {shortMoney(b.total)}</span>
                  </span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </Page>
  );
}
