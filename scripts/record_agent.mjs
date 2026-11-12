// Records the desk agent working Larkfield Market's queue, live, as one 1920x1080 video.
//
//   node scripts/record_agent.mjs --no-pay   # rehearsal: the pay tool is stubbed, no money is sent
//   node scripts/record_agent.mjs            # the recording: the agent pays Ana $212.75 through PayPal Payouts
//
// Off camera, through the API: start again, import the uncollected payouts from PayPal, open Ana's claim with her
// three documents and her PayPal address, open Mohammed's claim with the edited card (turned down), and approve Ana
// with a written reason. On camera: the Desk agent tab, "Work the queue" until the summary, then "Ask it to pay a
// turned-down claim" and the refusal.
//
// Writes web/shots/agent_raw.webm and web/shots/agent_steps.json (start and end, in seconds from the start of the
// video, of trace_start, paypal_lookup_visible, same_name_visible, pay_step_visible, paid_confirmed_visible,
// refusal_visible and summary_visible).
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(ROOT, "web", "package.json"));
const { chromium } = require("playwright");

const BASE = process.env.HOMING_URL || "http://127.0.0.1:8787";
const OUT = join(ROOT, "web", "shots");
const RAW = join(OUT, "agent-tmp");
const SAMPLES = join(ROOT, "data", "samples");
const PAY = !process.argv.includes("--no-pay");
const ZOOM = Number(process.env.HOMING_ZOOM || 1.25);
const W = 1920;
const H = 1080;
const ANA_ADDRESS = "sb-vgzm153858978@personal.example.com";
const REASON = "Marriage certificate registered in Chicago links Kowalski to Brennan, and the date of birth matches.";
rmSync(RAW, { recursive: true, force: true });
mkdirSync(RAW, { recursive: true });

async function api(method, path, body) {
  const init = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const r = await fetch(`${BASE}${path}`, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${method} ${path}: ${r.status} ${data.error || ""}`);
  return data;
}
async function documents(claimId, files) {
  const form = new FormData();
  for (const f of files) form.append("files[]", new Blob([readFileSync(join(SAMPLES, f))], { type: "image/png" }), f.split("/")[1]);
  return api("POST", `/api/claims/${claimId}/documents`, form);
}

// ---- off camera: the state the agent finds
console.log("preparing the desk");
await api("POST", "/api/demo/reset");
const imported = await api("POST", "/api/import/paypal");
console.log(`  imported ${imported.added} uncollected payouts from PayPal`);
let ana = await api("POST", "/api/claims", { balance_id: "B002", claimant: { name: "Ana Brennan", email: "ana.brennan@example.com",
  country: "IE", language: "en", former_names: ["Ana Kowalski"] } });
let mo = await api("POST", "/api/claims", { balance_id: "B007", claimant: { name: "Mohammed Al-Rashid", email: "m.alrashid@example.com",
  country: "AE", language: "ar" } });
[ana, mo] = await Promise.all([
  documents(ana.id, ["partial/id_card.png", "partial/utility_bill.png", "partial/marriage_certificate.png"]),
  documents(mo.id, ["edited/id_card.png"]),
]);
console.log(`  Ana ${ana.id}: ${ana.proof.verdict}; Mohammed ${mo.id}: ${mo.proof.verdict}`);
if (ana.proof.verdict !== "NEEDS_REVIEW" || mo.proof.verdict !== "REJECTED") throw new Error("the sample claims did not read as expected");
await api("POST", `/api/claims/${ana.id}/payout-address`, { receiver: ANA_ADDRESS });
await api("POST", `/api/claims/${ana.id}/approve`, { approver: "Larkfield claims desk", reason: REASON });
console.log("  Ana approved with a written reason, PayPal address on file");

// ---- on camera
const browser = await chromium.launch({ args: [`--window-size=${W},${H}`] });
const context = await browser.newContext({
  viewport: { width: W, height: H },
  deviceScaleFactor: 1,
  timezoneId: process.env.HOMING_TZ || "America/Panama", // UTC-5 all year
  recordVideo: { dir: RAW, size: { width: W, height: H } },
});
await context.addInitScript((zoom) => {
  const apply = () => {
    if (document.getElementById("demo-zoom")) return;
    const s = document.createElement("style");
    s.id = "demo-zoom";
    s.textContent = `html { zoom: ${zoom}; }`;
    document.head.appendChild(s);
  };
  if (document.head) apply();
  else document.addEventListener("DOMContentLoaded", apply);
}, ZOOM);
const t0 = Date.now();
const page = await context.newPage();
const sec = () => Math.round((Date.now() - t0) / 100) / 10;
const hold = (ms) => page.waitForTimeout(ms);
const moments = {};
const mark = (name) => {
  if (!moments[name]) {
    moments[name] = { start: sec(), end: null };
    console.log(`${sec().toFixed(1).padStart(6)} s  ${name}`);
  }
};
const close = (name) => {
  if (moments[name] && moments[name].end === null) moments[name].end = sec();
};

async function click(locator) {
  await locator.scrollIntoViewIfNeeded();
  await locator.hover();
  await hold(450);
  await locator.click();
}
// Keeps the newest row in view, gently, like someone following along.
async function followNewest() {
  await page.evaluate(() => {
    const rows = document.querySelectorAll(".agent-step, .agent-summary, .agent-thinking");
    const last = rows[rows.length - 1];
    if (!last) return;
    const r = last.getBoundingClientRect();
    if (r.bottom > innerHeight - 40 || r.top < 80) window.scrollBy({ top: r.bottom - innerHeight + 160, behavior: "smooth" });
  });
}

await page.goto(`${BASE}/${PAY ? "" : "?rehearsal=1"}#/holder/balances`);
await page.locator("h1").first().waitFor();
await hold(2500);
await click(page.getByRole("tab", { name: "Desk agent" }));
await hold(2500);
await click(page.getByRole("button", { name: "Work the queue" }));
mark("trace_start");

const deadline = Date.now() + 15 * 60_000;
let lastCount = 0;
for (;;) {
  if (Date.now() > deadline) throw new Error("the queue run took too long");
  await hold(700);
  const state = await page.evaluate(() => {
    const steps = [...document.querySelectorAll(".agent-step")].map((el) => ({
      tool: el.getAttribute("data-tool"), outcome: el.getAttribute("data-outcome"), text: el.textContent || "" }));
    return { steps, summary: !!document.querySelector('[data-testid="agent-summary"]') };
  });
  const s = state.steps;
  if (s.some((x) => x.tool === "paypal_payout_item" && x.outcome === "allowed")) mark("paypal_lookup_visible");
  if (s.some((x) => x.tool === "ledger_lookup" && x.outcome === "allowed")) { close("paypal_lookup_visible"); mark("same_name_visible"); }
  if (s.some((x) => x.tool === "pay_claim" && x.outcome !== "running")) { close("same_name_visible"); mark("pay_step_visible"); }
  if (s.some((x) => x.tool === "wait_for_paypal" && x.outcome === "allowed" && (x.text.includes("CLAIMED") || x.text.includes("Rehearsal")))) {
    close("pay_step_visible");
    mark("paid_confirmed_visible");
  }
  if (s.length !== lastCount) {
    lastCount = s.length;
    await followNewest();
  }
  if (state.summary) {
    close("paypal_lookup_visible");
    close("same_name_visible");
    close("pay_step_visible");
    await hold(1500);
    close("paid_confirmed_visible");
    const summary = page.getByTestId("agent-summary");
    await summary.scrollIntoViewIfNeeded();
    mark("summary_visible");
    await hold(6000);
    close("summary_visible");
    break;
  }
}

// prove the brakes
await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
await hold(1500);
await click(page.getByRole("button", { name: "Ask it to pay a turned-down claim" }));
for (;;) {
  if (Date.now() > deadline) throw new Error("the brakes run took too long");
  await hold(700);
  const refused = page.locator('.agent-step[data-outcome="refused"]').first();
  if (await refused.isVisible().catch(() => false)) {
    await refused.scrollIntoViewIfNeeded();
    mark("refusal_visible");
    break;
  }
  const done = await page.getByTestId("agent-summary").isVisible().catch(() => false);
  if (done) throw new Error("the brakes run finished without a refusal on screen");
}
await page.getByTestId("agent-summary").waitFor({ timeout: 120_000 }).catch(() => undefined);
await followNewest();
await hold(6000);
close("refusal_visible");

await context.close();
await browser.close();
const file = readdirSync(RAW).find((f) => f.endsWith(".webm"));
renameSync(join(RAW, file), join(OUT, "agent_raw.webm"));
rmSync(RAW, { recursive: true, force: true });
const order = ["trace_start", "paypal_lookup_visible", "same_name_visible", "pay_step_visible", "paid_confirmed_visible", "refusal_visible", "summary_visible"];
const trace = moments.trace_start;
if (trace) trace.end = moments.summary_visible?.end ?? sec();
const out = { paying: PAY, ana_claim: ana.id, mohammed_claim: mo.id, moments: Object.fromEntries(order.map((k) => [k, moments[k] || null])) };
writeFileSync(join(OUT, "agent_steps.json"), JSON.stringify(out, null, 2) + "\n");
console.log(JSON.stringify(out, null, 2));
