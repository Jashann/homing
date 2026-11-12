// Records the demo as one continuous 1920x1080 video with human pacing, against the running server, the real
// document reading (the model) and the real PayPal sandbox.
//
//   HOMING_DEMO=1 uv run python -m homing.server                        # in one terminal
//   node scripts/record_demo.mjs --scenes scripts/demo_scenes.json --no-pay   # rehearsal: no payout is sent
//   node scripts/record_demo.mjs --scenes scripts/demo_scenes.json            # the recording, with two payouts
//
// --scenes names a JSON file of minimum scene durations in seconds, with the keys opening, find, check, pay, ana,
// edited, uncollected, invoice and closing. Each scene is held on screen for at least that long.
//
// Scenes:
//   opening      the California number and the size bands
//   find         the business's desk: Import uncollected payouts from PayPal, then a notice drafted in Polish for
//                Tomasz; then the owner's side: Tomasz's search and result, then the same-name search (Wei Zhang)
//   check        Tomasz's two documents, the reading state, what was read, a scroll that pauses on the comparison
//   pay          the test receiver, send, wait for PayPal, the Paid screen, then "See PayPal's record"
//   ana          three documents, a person must decide, the business types the reason and approves, her page updates
//                (Ana is not paid)
//   edited       Mohammed's edited card: turned down with nothing revealed; the business sees the signs of editing
//   uncollected  J. R. Okafor paid to a made-up address: not collected, then "Use a different address" is refused
//                while PayPal holds the first payment
//   invoice      the business's invoices with Tomasz's fee, PayPal's own invoice page, then the results
//   closing      back to the opening number
//
// The paying run sends two payouts from the business's sandbox balance: $86.40 to the first test receiver (Tomasz,
// collected) and $640.00 to a made-up address (Okafor, held by PayPal for 30 days). --no-pay does everything else and
// stops at each filled payment form. It starts with POST /api/demo/reset.
//
// Writes web/shots/demo_raw.webm and web/shots/demo_steps.json: the start and end of every scene and of the marked
// moments paid_screen_visible, paypal_record_open and invoice_row_visible, in seconds from the start of the video.
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(ROOT, "web", "package.json"));
const { chromium } = require("playwright");

const SCENES = ["opening", "find", "check", "pay", "ana", "edited", "uncollected", "invoice", "closing"];
const arg = (name) => {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
};
const scenesFile = arg("--scenes");
if (!scenesFile) {
  console.error("usage: node scripts/record_demo.mjs --scenes <file.json> [--no-pay]");
  process.exit(2);
}
const minimum = JSON.parse(readFileSync(scenesFile, "utf8"));
const missing = SCENES.filter((s) => typeof minimum[s] !== "number");
if (missing.length) {
  console.error(`${scenesFile} needs a number of seconds for: ${missing.join(", ")}`);
  process.exit(2);
}

const BASE = process.env.HOMING_URL || "http://127.0.0.1:8787";
const OUT = join(ROOT, "web", "shots");
const RAW = join(OUT, "video-tmp");
const SAMPLES = join(ROOT, "data", "samples");
const PAY = !process.argv.includes("--no-pay");
const HOLDER = "Larkfield Market";
const ZOOM = Number(process.env.HOMING_ZOOM || 1.25); // page zoom, so body text reads large in the video
const W = 1920;
const H = 1080;
const READ_MS = 300_000;
const PAYPAL_MS = 300_000;
mkdirSync(RAW, { recursive: true });

const reset = await fetch(`${BASE}/api/demo/reset`, { method: "POST" }).catch(() => null);
if (!reset?.ok) throw new Error("the server did not reset; is it running?");
const meta = await (await fetch(`${BASE}/api/meta`)).json();
const TOMASZ_RECEIVER = process.env.HOMING_SANDBOX_RECEIVER || meta.test_receivers[0];

const browser = await chromium.launch({ args: [`--window-size=${W},${H}`] });
const context = await browser.newContext({
  viewport: { width: W, height: H },
  deviceScaleFactor: 1,
  // UTC-5 all year (Winnipeg's offset since November 2026; Chromium's zone data still has the old rule)
  timezoneId: process.env.HOMING_TZ || "America/Panama",
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
const t0 = Date.now(); // the video starts with the context
const page = await context.newPage();
const sec = () => Math.round((Date.now() - t0) / 100) / 10;

const scenes = [];
const moments = [];
let scene = null;
function begin(name) {
  scene = { name, start: sec(), minimum: minimum[name] };
  console.log(`${scene.start.toFixed(1).padStart(6)} s  ${name}`);
}
async function finish() {
  const left = scene.minimum - (sec() - scene.start);
  if (left > 0) await page.waitForTimeout(left * 1000);
  scene.end = sec();
  scenes.push(scene);
  console.log(`${scene.end.toFixed(1).padStart(6)} s  ${scene.name} ends (${(scene.end - scene.start).toFixed(1)} s)`);
}
const open = {};
function momentStart(name) {
  open[name] = sec();
  console.log(`${open[name].toFixed(1).padStart(6)} s    moment ${name}`);
}
function momentEnd(name) {
  if (open[name] === undefined) return;
  moments.push({ name, start: open[name], end: sec() });
  delete open[name];
}

const hold = (ms) => page.waitForTimeout(ms);
const rnd = (a, b) => a + Math.random() * (b - a);
const pause = () => hold(rnd(900, 1600));

async function type(locator, text) {
  await locator.scrollIntoViewIfNeeded();
  await locator.click();
  await hold(220);
  await locator.pressSequentially(text, { delay: rnd(55, 85) });
  await hold(320);
}

async function clear(locator) {
  await locator.click();
  await hold(150);
  await locator.press("ControlOrMeta+a");
  await locator.press("Backspace");
  await hold(200);
}

async function click(locator) {
  await locator.scrollIntoViewIfNeeded();
  await locator.hover();
  await hold(380);
  await locator.click();
}

// Smooth scroll, like a trackpad: to a y position in the page, or by some pixels.
async function glide(target) {
  await page.evaluate(async (target) => {
    const start = window.scrollY;
    const max = document.documentElement.scrollHeight - innerHeight;
    const distance = Math.max(0, Math.min(target, max)) - start;
    const frames = Math.max(24, Math.round(Math.abs(distance) / 11));
    for (let i = 1; i <= frames; i++) {
      const t = i / frames;
      window.scrollTo(0, start + distance * (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2));
      await new Promise((r) => requestAnimationFrame(r));
    }
  }, target);
  await hold(250);
}
const top = () => glide(0);
async function glideTo(locator, offset = 110) {
  const y = await locator.evaluate((el, offset) => el.getBoundingClientRect().top + window.scrollY - offset, offset);
  await glide(y);
}

const h1 = () => page.locator("h1").first();
const toBusiness = () => click(page.getByRole("tab", { name: "I hold the money" }));
const toOwner = () => click(page.getByRole("tab", { name: "I am owed money" }));
const claimId = () => page.url().split("/").pop();

// Fills the search form from scratch: an earlier search's details are cleared first.
async function search({ name, former, email, city, country }) {
  const back = page.getByRole("button", { name: "Change my details" });
  if (await back.isVisible().catch(() => false)) await click(back);
  const fields = [
    [page.getByPlaceholder("For example, Amara Okafor"), name],
    [page.getByPlaceholder("For example, Amara Nwosu").first(), former],
    [page.getByPlaceholder("name@example.com").first(), email],
    [page.getByPlaceholder("City").first(), city],
    [page.getByPlaceholder("Country").first(), country],
  ];
  for (const [box, value] of fields) {
    await box.waitFor();
    if (await box.inputValue()) await clear(box);
    if (value) await type(box, value);
  }
  await pause();
  await click(page.getByRole("button", { name: "Look for my money" }));
  await page.locator("article.match").first().waitFor();
}

async function startClaim(person, band) {
  await click(page.locator(".wordmark"));
  await click(page.getByRole("button", { name: "Find mine" }));
  await search(person);
  await hold(2200);
  await click(page.locator("article.match", { hasText: band }).getByRole("button", { name: "This is mine" }));
  await page.getByText("Check your documents against the record.").waitFor();
  await pause();
}

async function upload(files, verdict) {
  await page.locator('input[type="file"]').setInputFiles(files.map((f) => join(SAMPLES, f)));
  await hold(1800);
  await click(page.getByRole("button", { name: /^Check my/ }));
  await page.getByText("Reading the documents").waitFor({ timeout: 5000 }).catch(() => undefined);
  await h1().filter({ hasText: verdict }).waitFor({ timeout: READ_MS });
}

async function payForm(receiver) {
  await click(page.getByRole("button", { name: "Continue to payment" }));
  const box = page.getByPlaceholder("name@example.com or +1 416 555 0182");
  await box.waitFor();
  await pause();
  await clear(box);
  await type(box, receiver);
  await pause();
}

let tomaszClaim = null;
try {
  // ---- opening: the number and the bands
  begin("opening");
  await page.goto(`${BASE}/#/`);
  await page.getByRole("heading", { name: /\$125,083,033/ }).waitFor();
  await hold(4000);
  await glideTo(page.locator(".bands"), 260);
  await hold(4000);
  await top();
  await finish();

  // ---- find: the business imports its uncollected PayPal payouts and drafts a notice; the owner searches
  begin("find");
  await toBusiness();
  await page.locator("table.table tbody tr").first().waitFor();
  await hold(1500);
  await click(page.getByRole("button", { name: "Import uncollected payouts from PayPal" }));
  await page.getByText(/^PayPal checked \d+ payouts? from /).waitFor({ timeout: 60_000 });
  await hold(1200);
  await glideTo(page.locator(".tag-paypal").first(), 300);
  await hold(4000);
  const row = page.locator("tr", { hasText: "Tomasz W." });
  await glideTo(row, 300);
  await click(row.getByRole("button", { name: "Draft notice" }));
  await page.getByText("In Polish").waitFor({ timeout: READ_MS });
  await glideTo(page.locator(".notice"), 150);
  await hold(6000);
  await toOwner();
  await click(page.getByRole("button", { name: "Find mine" }));
  await search({ name: "Tomasz Wiśniewski", email: "t.wisniewski@example.com", city: "Kraków", country: "Poland" });
  await hold(4500);
  await click(page.locator("article.match", { hasText: /between \$50 and \$100/i }).getByRole("button", { name: "This is mine" }));
  await page.getByText("Check your documents against the record.").waitFor();
  tomaszClaim = claimId();
  await hold(1500);
  await click(page.getByRole("button", { name: /Find yours/ }));
  await search({ name: "Wei Zhang", city: "Toronto", country: "Canada" });
  await page.locator("article.match").nth(1).waitFor();
  await hold(3500);
  await glideTo(page.locator("article.match").nth(1), 200);
  await hold(3000);
  await top();
  await finish();

  // ---- check: Tomasz's documents are read and compared
  begin("check");
  await page.evaluate((id) => {
    window.location.hash = `#/prove/${id}`;
  }, tomaszClaim);
  await page.getByText("Check your documents against the record.").waitFor();
  await pause();
  await upload(["clean/id_card.png", "clean/old_statement.png"], "Checked. Your documents match the record.");
  await hold(3500);
  await glideTo(page.locator(".claimant-docs"), 90);
  await hold(4500);
  await glideTo(page.locator(".compared"), 90);
  await hold(5000);
  await top();
  await finish();

  // ---- pay: Tomasz is paid to the sandbox test account, then PayPal's own record
  begin("pay");
  await payForm(TOMASZ_RECEIVER);
  if (PAY) {
    await click(page.getByRole("button", { name: "Send it home" }));
    await page.getByText(/is on its way\./).waitFor({ timeout: 60_000 });
    await h1().filter({ hasText: "Paid." }).waitFor({ timeout: PAYPAL_MS });
    momentStart("paid_screen_visible");
    await hold(4500);
    await click(page.getByRole("button", { name: "See PayPal's record" }));
    await page.getByText("This is PayPal's answer, fetched just now").waitFor({ timeout: 60_000 });
    await glideTo(page.locator(".paypal-record"), 160);
    momentStart("paypal_record_open");
    await hold(6000);
  } else {
    await hold(3000);
  }
  await finish();
  momentEnd("paypal_record_open");
  momentEnd("paid_screen_visible");

  // ---- ana: a person at the business decides, with a written reason
  begin("ana");
  await startClaim({ name: "Ana Brennan", former: "Ana Kowalski", city: "Chicago", country: "United States" }, /between \$100 and \$250/i);
  await upload(["partial/id_card.png", "partial/utility_bill.png", "partial/marriage_certificate.png"], `A person at ${HOLDER} must approve this.`);
  await hold(3500);
  await glideTo(page.locator(".compared"), 90);
  await hold(3500);
  await top();
  await toBusiness();
  await click(page.getByRole("tab", { name: /Waiting for approval/ }));
  const approve = page.getByRole("button", { name: "Approve", exact: true });
  await approve.waitFor();
  await hold(2500);
  await type(
    page.getByPlaceholder("Say which documents satisfied you, and why."),
    "Marriage certificate registered in Chicago links Kowalski to Brennan, and the date of birth matches.",
  );
  await hold(1200);
  await click(approve);
  await page.getByText(/^Approved by Claims desk\./).waitFor();
  await hold(2500);
  await toOwner();
  await page.getByRole("heading", { name: `Approved by a person at ${HOLDER}.` }).waitFor({ timeout: 30_000 });
  await hold(4500);
  await finish();

  // ---- edited: turned down, nothing revealed to the claimant; the business sees the signs of editing
  begin("edited");
  await startClaim({ name: "Mohammed Al-Rashid", city: "Dubai", country: "United Arab Emirates" }, /between \$1,000 and \$2,500/i);
  await upload(["edited/id_card.png"], "We could not confirm this claim.");
  await hold(5000);
  await toBusiness();
  await click(page.getByRole("tab", { name: /Waiting for approval/ }));
  const card = page.locator("article.review", { hasText: "Mohammed Al-Rashid" });
  await card.waitFor();
  await glideTo(card.locator(".doc-card-edited"), 140);
  await hold(5500);
  await top();
  await finish();

  // ---- uncollected: a different owner, paid to a made-up address; a different address is refused
  begin("uncollected");
  await toOwner();
  await startClaim({ name: "J. R. Okafor", city: "Lagos", country: "Nigeria" }, /between \$500 and \$1,000/i);
  await upload(["uncollected/id_card.png", "uncollected/old_statement.png"], "Checked. Your documents match the record.");
  await hold(2500);
  await payForm(`jr.okafor.${Math.random().toString(36).slice(2, 7)}@example.com`);
  if (PAY) {
    await click(page.getByRole("button", { name: "Send it home" }));
    await page.getByRole("heading", { name: /^Not collected yet/ }).waitFor({ timeout: PAYPAL_MS });
    await hold(4500);
    await click(page.getByRole("button", { name: "Use a different address" }));
    await type(page.getByPlaceholder("name@example.com or +1 416 555 0182"), `okafor.new.${Math.random().toString(36).slice(2, 7)}@example.com`);
    await pause();
    await click(page.getByRole("button", { name: "Send to this address" }));
    await page.getByRole("heading", { name: /^Held by PayPal/ }).waitFor({ timeout: 90_000 });
    await hold(5000);
  } else {
    await hold(3000);
  }
  await finish();

  // ---- invoice: Tomasz's fee invoice, PayPal's own invoice page, then the results
  begin("invoice");
  await top();
  await toBusiness();
  await click(page.getByRole("tab", { name: "Invoices" }));
  if (PAY) {
    const inv = page.locator("table.table tbody tr", { hasText: "Tomasz" });
    await inv.waitFor({ timeout: 60_000 });
    momentStart("invoice_row_visible");
    await hold(4000);
    const href = await inv.getByRole("link", { name: "Open in PayPal" }).getAttribute("href");
    await inv.getByRole("link", { name: "Open in PayPal" }).hover();
    await hold(800);
    momentEnd("invoice_row_visible");
    await page.goto(href); // the same view: PayPal's own page for the invoice
    await page.waitForLoadState("domcontentloaded");
    await hold(6000);
    await page.goBack();
    await page.locator("table.table").first().waitFor();
  } else {
    await page.getByText("No invoices yet.").waitFor();
    await hold(2500);
  }
  await click(page.getByRole("tab", { name: "Results" }));
  await page.getByText("Paid home").waitFor();
  await hold(4500);
  await finish();

  // ---- closing: back to the opening number
  begin("closing");
  await click(page.locator(".wordmark"));
  await page.getByRole("heading", { name: /\$125,083,033/ }).waitFor();
  await hold(1000);
  await finish();
} catch (e) {
  const where = scene ? scene.name : "start";
  console.error(`stopped in ${where}: ${String(e.message || e).split("\n")[0]}`);
  if (scene && !scene.end) scenes.push({ ...scene, end: sec(), stopped: String(e.message || e).split("\n")[0] });
  process.exitCode = 1;
} finally {
  for (const name of Object.keys(open)) momentEnd(name);
  const video = page.video();
  await context.close();
  const raw = join(OUT, "demo_raw.webm");
  rmSync(raw, { force: true });
  await video.saveAs(raw);
  await video.delete();
  await browser.close();
  rmSync(RAW, { recursive: true, force: true });
  const steps = { video: "demo_raw.webm", width: W, height: H, zoom: ZOOM, paying: PAY, scenes, moments };
  writeFileSync(join(OUT, "demo_steps.json"), JSON.stringify(steps, null, 2) + "\n");
  console.log(`wrote ${raw} and web/shots/demo_steps.json`);
}
