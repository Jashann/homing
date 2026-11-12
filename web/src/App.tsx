import { useEffect, useState } from "react";
import type { Match, Meta } from "./types";
import { getMeta } from "./api";
import { DemoBanner, FALLBACK_META, MetaContext, TopBar, type StepId } from "./ui";
import Opening from "./screens/Opening";
import Find, { emptyForm, type FindForm } from "./screens/Find";
import Prove from "./screens/Prove";
import Pay from "./screens/Pay";
import Holder, { type HolderTab } from "./screens/Holder";

// Where the person is, kept after the "#" so a reload lands on the same screen:
// "#/", "#/find", "#/prove/<claim>", "#/pay/<claim>", "#/holder/<tab>".
type Route =
  | { name: "opening" }
  | { name: "find" }
  | { name: "prove"; id: string }
  | { name: "pay"; id: string }
  | { name: "holder"; tab: HolderTab };

const TABS: HolderTab[] = ["balances", "approvals", "agent", "invoices", "results"];

function readHash(): Route {
  const [name, arg] = window.location.hash.replace(/^#\/?/, "").split("/");
  if (name === "find") return { name: "find" };
  if (name === "prove" && arg) return { name: "prove", id: arg };
  if (name === "pay" && arg) return { name: "pay", id: arg };
  if (name === "holder") return { name: "holder", tab: TABS.includes(arg as HolderTab) ? (arg as HolderTab) : "balances" };
  return { name: "opening" };
}

function hashOf(r: Route): string {
  if (r.name === "opening") return "#/";
  if (r.name === "find") return "#/find";
  if (r.name === "holder") return `#/holder/${r.tab}`;
  return `#/${r.name}/${r.id}`;
}

const TITLES: Record<Route["name"], string> = {
  opening: "Homing",
  find: "Homing: find your money",
  prove: "Homing: check your documents",
  pay: "Homing: send it home",
  holder: "Homing: the business's desk",
};

export default function App() {
  const [route, setRoute] = useState<Route>(readHash);
  const [form, setForm] = useState<FindForm>(emptyForm);
  const [matches, setMatches] = useState<Match[] | null>(null);
  const [lastClaim, setLastClaim] = useState<string | null>(null);
  const [lastOwner, setLastOwner] = useState<Route>({ name: "opening" });
  const [meta, setMeta] = useState<Meta>(FALLBACK_META);

  useEffect(() => {
    getMeta().then(setMeta).catch(() => undefined);
  }, []);

  useEffect(() => {
    const follow = () => setRoute(readHash());
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, []);

  useEffect(() => {
    const h = hashOf(route);
    if (window.location.hash !== h) window.history.pushState(null, "", `${window.location.pathname}${window.location.search}${h}`);
    document.title = TITLES[route.name];
    if (route.name === "prove" || route.name === "pay") setLastClaim(route.id);
    if (route.name !== "holder") setLastOwner(route);
    window.scrollTo(0, 0);
  }, [route]);

  const view = route.name === "holder" ? "holder" : "owner";
  const step: StepId | undefined =
    route.name === "opening" ? "money" : route.name === "find" ? "find" : route.name === "prove" ? "prove" : route.name === "pay" ? "pay" : undefined;

  const onStep: Partial<Record<StepId, () => void>> = {
    money: () => setRoute({ name: "opening" }),
    find: () => setRoute({ name: "find" }),
    ...(lastClaim ? { prove: () => setRoute({ name: "prove", id: lastClaim }) } : {}),
  };

  return (
    <MetaContext.Provider value={meta}>
      {(meta.demo || meta.public) && <DemoBanner isPublic={meta.public} />}
      <TopBar
        view={view}
        step={step}
        onStep={onStep}
        onHome={() => setRoute({ name: "opening" })}
        onView={(v) => setRoute(v === "holder" ? { name: "holder", tab: route.name === "holder" ? route.tab : "balances" } : lastOwner)}
      />
      {route.name === "opening" && <Opening onBegin={() => setRoute({ name: "find" })} />}
      {route.name === "find" && (
        <Find form={form} setForm={setForm} matches={matches} setMatches={setMatches} onClaimed={(id) => setRoute({ name: "prove", id })} />
      )}
      {route.name === "prove" && (
        <Prove key={route.id} claimId={route.id} onPay={() => setRoute({ name: "pay", id: route.id })} onBack={() => setRoute({ name: "find" })} />
      )}
      {route.name === "pay" && (
        <Pay key={route.id} claimId={route.id} onProof={() => setRoute({ name: "prove", id: route.id })} onDone={() => {
          setMatches(null);
          setForm(emptyForm());
          setRoute({ name: "opening" });
        }} />
      )}
      {route.name === "holder" && <Holder tab={route.tab} onTab={(tab) => setRoute({ name: "holder", tab })} />}
    </MetaContext.Provider>
  );
}
