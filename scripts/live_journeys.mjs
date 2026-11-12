// Drives the whole product in headless Chromium against the running server, the real document reading (the model)
// and the real PayPal sandbox, and saves a 1280x720 screenshot of every step.
//
//   HOMING_DEMO=1 uv run python -m homing.server     # in one terminal (serves web/dist on port 8787)
//   node scripts/live_journeys.mjs --no-pay          # everything except payouts -> web/shots/live2/no-pay/
//   node scripts/live_journeys.mjs --pay             # the full run, with real sandbox payouts -> web/shots/live2/
//   node scripts/live_journeys.mjs --reshoot         # screenshots of the claims already stored; nothing is sent
//
// --pay spends the business's sandbox balance: J. R. Okafor $640.00 to a made-up address (never collected, held by
// PayPal), Tomasz $86.40 and Ana $212.75 to the two sandbox personal accounts. Run it once.
// Both paying and no-pay modes start with POST /api/demo/reset. HOMING_URL overrides http://127.0.0.1:8787 and
// HOMING_SHOTS the screenshot folder.
// Exit code 0 only when every journey passed.
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(ROOT, "web", "package.json"));
const { chromium } = require("playwright");

const MODE = process.argv.includes("--pay") ? "pay" : process.argv.includes("--reshoot") ? "reshoot" : "no-pay";
const PAY = MODE === "pay";
const BASE = process.env.HOMING_URL || "http://127.0.0.1:8787";
const SHOTS = process.env.HOMING_SHOTS || (MODE === "no-pay" ? join(ROOT, "web", "shots", "live2", "no-pay") : join(ROOT, "web", "shots", "live2"));
const SAMPLES = join(ROOT, "data", "samples");
const READ_MS = 300_000; // the model reads each document
const PAYPAL_MS = 300_000; // the sandbox moves a payout from PENDING to SUCCESS or UNCLAIMED
const HOLDER = "Larkfield Market";
const BOX = "name@example.com or +1 416 555 0182";
mkdirSync(SHOTS, { recursive: true });

const results = [];
const problems = [];
const facts = { mode: MODE, started: new Date().toISOString() };

function check(ok, what) {
  if (!ok) throw new Error(`check failed: ${what}`);
}

async function api(method, path, body) {
  const r = await fetch(BASE + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: r.status, body: await r.json().catch(() => null) };
}

const shots = [];
async function shot(page, name, full = false) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: full });
  shots.push(`${name}.png`);
}

function watch(page, label) {
  // 4xx answers are expected (refused payments, a missing reason); they are shown on the page as sentences
  page.on("console", (m) => m.type() === "error" && !/40\d/.test(m.text()) && problems.push(`${label} console: ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`${label} page error: ${e.message}`));
  page.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 500) problems.push(`${label} ${r.status()} ${r.url()}`);
  });
}

async function journey(name, fn) {
  const t0 = Date.now();
  try {
    await fn();
    results.push({ name, ok: true, seconds: Math.round((Date.now() - t0) / 1000) });
    console.log(`PASS ${name} (${Math.round((Date.now() - t0) / 1000)} s)`);
  } catch (e) {
    results.push({ name, ok: false, error: String(e.message || e) });
    console.log(`FAIL ${name}: ${e.message || e}`);
  }
}

const claimIdOf = (page) => page.url().split("/").pop();

// A fresh load, so no screen keeps what an earlier journey typed.
async function open(page, hash) {
  await page.goto("about:blank");
  await page.goto(`${BASE}/${hash}`);
  await page.locator("main").first().waitFor();
}
const heading = (page) => page.locator("h1").first();

async function fillFind(page, { name, former, email, city, country }) {
  await open(page, "#/find");
  await page.getByPlaceholder("For example, Amara Okafor").fill(name);
  if (former) await page.getByPlaceholder("For example, Amara Nwosu").fill(former);
  if (email) await page.getByPlaceholder("name@example.com").fill(email);
  await page.getByPlaceholder("City").first().fill(city || "");
  await page.getByPlaceholder("Country").first().fill(country || "");
}

async function searchFor(page, person, prefix) {
  await fillFind(page, person);
  if (prefix) await shot(page, `${prefix}-find-filled`);
  await page.getByRole("button", { name: "Look for my money" }).click();
  await page.locator("article.match").first().waitFor();
  if (prefix) await shot(page, `${prefix}-results`);
}

async function findAndClaim(page, person, cardText, prefix) {
  await searchFor(page, person, prefix);
  const card = page.locator("article.match", { hasText: cardText });
  page.lastBand = (await card.count()) === 1 ? await card.locator(".likely-words").innerText() : "";
  check((await card.count()) === 1, `one result card shows ${cardText}`);
  await card.getByRole("button", { name: "This is mine" }).click();
  await page.getByText("Check your documents against the record.").waitFor();
  if (prefix) await shot(page, `${prefix}-prove-empty`);
  return claimIdOf(page);
}

async function upload(page, files, prefix) {
  await page.locator('input[type="file"]').setInputFiles(files.map((f) => join(SAMPLES, f)));
  if (prefix) await shot(page, `${prefix}-documents-added`);
  await page.getByRole("button", { name: /^Check my/ }).click();
  await page.getByText("Reading the documents").waitFor({ timeout: 5000 }).catch(() => undefined);
  if (prefix) await shot(page, `${prefix}-reading`);
}

async function payForm(page, receiver, prefix) {
  await page.getByRole("button", { name: "Continue to payment" }).click();
  const box = page.getByPlaceholder(BOX);
  await box.fill(receiver);
  if (prefix) await shot(page, `${prefix}-pay-form`);
}

async function send(page) {
  await page.getByRole("button", { name: "Send it home" }).click();
  await page.getByText(/is on its way\.|^Paid\.|^Not collected/).first().waitFor({ timeout: 60_000 });
}

// "Paid" must appear only after PayPal reports the item as collected.
async function waitPaid(page, claimId, prefix) {
  const end = Date.now() + PAYPAL_MS;
  let sawSent = false;
  while (Date.now() < end) {
    const text = await page.locator("main").innerText();
    const h = text.trim().split("\n")[0];
    const c = (await api("GET", `/api/claims/${claimId}`)).body;
    if (h.startsWith("Paid.")) {
      check(c.payout.state === "CLAIMED", `the page says Paid only when PayPal says CLAIMED (was ${c.payout.state})`);
      return sawSent;
    }
    if (h.includes("is on its way")) {
      check(!/is in your PayPal account|^Paid\./m.test(text), "nothing says paid while it is on its way");
      if (!sawSent && prefix) await shot(page, `${prefix}-on-its-way`);
      sawSent = true;
    }
    check(!h.startsWith("Not collected"), `payout to a sandbox account went uncollected (${c.payout.state})`);
    await page.waitForTimeout(1000);
  }
  throw new Error("PayPal did not confirm the payout in time");
}

/** When and how the payout was confirmed, from the claim's timeline. */
function confirmation(c) {
  const sent = c.payout.timeline.find((t) => t.title === "Sent through PayPal");
  const done = [...c.payout.timeline].reverse().find((t) => ["Collected", "Not collected yet"].includes(t.title));
  return {
    claim: c.id,
    state: c.payout.state,
    confirmed_by: c.payout.confirmed_by,
    sent_at: sent?.at,
    final_at: done?.at,
    seconds_from_send: sent && done ? Math.round((new Date(done.at) - new Date(sent.at)) / 1000) : null,
    titles: c.payout.timeline.map((t) => `${t.title}${t.heard_by ? ` [${t.heard_by}]` : ""}`),
  };
}

async function waitInvoice(claimId) {
  for (let i = 0; i < 40; i++) {
    const c = (await api("GET", `/api/claims/${claimId}`)).body;
    if (c.invoice && c.payout.timeline.some((t) => t.title === `Fee invoice sent to ${HOLDER}`)) return c;
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error(`no fee invoice was sent to ${HOLDER}`);
}

// The proof screen at 1280x720: the comparison table must be one short scroll away.
async function tableReach(page) {
  const top = await page.locator(".compared").evaluate((el) => el.getBoundingClientRect().top + window.scrollY);
  check(top < 720 + 560, `the comparison table starts ${Math.round(top)} px down, more than one short scroll`);
  return Math.round(top);
}

async function textOf(page) {
  return page.locator("main").innerText();
}

const browser = await chromium.launch();
// Times are shown in the viewer's zone. Manitoba moved to UTC-5 for good in November 2026, but Chromium's zone data
// still says UTC-6 for America/Winnipeg, so the screenshots use a zone with the same offset (HOMING_TZ overrides).
const context = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  deviceScaleFactor: 1,
  timezoneId: process.env.HOMING_TZ || "America/Toronto",
});
const owner = await context.newPage();
const holder = await context.newPage();
watch(owner, "owner");
watch(holder, "holder");

const meta = (await api("GET", "/api/meta")).body;
check(meta && meta.demo, "the server runs with HOMING_DEMO=1");
const [RECEIVER_TOMASZ, RECEIVER_ANA] = meta.test_receivers;

const tomasz = { name: "Tomasz Wiśniewski", email: "t.wisniewski@example.com", city: "Kraków", country: "Poland" };
const ana = { name: "Ana Brennan", former: "Ana Kowalski", city: "Chicago", country: "United States" };

if (MODE === "reshoot") {
  // Screens of the claims already stored, without sending anything.
  const claims = (await api("GET", "/api/claims")).body.claims;
  for (const c of claims) {
    const who = c.claimant.name.split(" ")[0].toLowerCase();
    await open(owner, `#/prove/${c.id}`);
    await heading(owner).waitFor();
    await shot(owner, `re-${who}-${c.balance_id}-proof`);
    if (c.payout.state !== "NONE") {
      await open(owner, `#/pay/${c.id}`);
      await heading(owner).waitFor();
      await shot(owner, `re-${who}-${c.balance_id}-pay`);
      await shot(owner, `re-${who}-${c.balance_id}-pay-full`, true);
    }
  }
  for (const tab of ["balances", "approvals", "invoices", "results"]) {
    await open(holder, `#/holder/${tab}`);
    await holder.waitForTimeout(800);
    await shot(holder, `re-holder-${tab}`);
  }
  await browser.close();
  console.log(JSON.stringify({ shots }, null, 2));
  process.exit(0);
}

const reset = await api("POST", "/api/demo/reset");
check(reset.status === 200, "demo reset");

// 01. The opening: the California number and where it comes from.
await journey("opening", async () => {
  await open(owner, "#/");
  await owner.getByRole("heading", { name: /\$125,083,033/ }).waitFor();
  const text = await textOf(owner);
  check(text.includes("checks their documents against the account record"), "the tagline");
  check(!/Not only people|charit/i.test(text), "no list of organisations");
  await shot(owner, "01-opening");
  await owner.evaluate(() => window.scrollTo(0, document.querySelector(".bands").getBoundingClientRect().top + window.scrollY - 200));
  await shot(owner, "01-opening-bands");
});

// 02. Same-name search: a common name and one city gives two "possible" balances, never a sure one.
await journey("same name", async () => {
  await searchFor(owner, { name: "Wei Zhang", city: "Toronto" }, null);
  const cards = owner.locator("article.match");
  check((await cards.count()) === 2, "two matches");
  check((await owner.getByText("Very likely yours").count()) === 0, "neither is called very likely");
  check((await owner.getByText(/^Another balance has this name, in /).count()) === 2, "both say another balance has this name");
  const text = await textOf(owner);
  check(/More than one person on the list has this name/.test(text), "the lead explains the same name");
  check(!/Vancouver/.test(text) && !/\$1,240|\$57\.10/.test(text), "no untyped city and no exact amount before a claim");
  check(/between \$1,000 and \$2,500/i.test(text) && /between \$50 and \$100/i.test(text), "amounts shown as bands");
  await shot(owner, "02-same-name");
  await shot(owner, "02-same-name-full", true);
});

// 03. A balance already with the state: the owner gets a claim packet addressed to them.
await journey("claim packet", async () => {
  await searchFor(owner, { name: "Rosa Delgado", city: "Fresno", country: "United States" }, null);
  const card = owner.locator("article.match", { hasText: /between \$250 and \$500/i });
  check(/Sent to the State of California on 15 April 2019/.test(await card.innerText()), "the date it went to the state");
  check((await card.getByRole("button", { name: "This is mine" }).count()) === 0, "no claim button for a state-held balance");
  await shot(owner, "03-with-the-state");
  await card.getByRole("button", { name: "Prepare claim packet" }).click();
  const link = owner.getByRole("link", { name: "Open the claim packet (PDF)" });
  await link.waitFor({ timeout: 30_000 });
  const steps = await owner.locator(".steps-list li").allInnerTexts();
  check(steps.length >= 6, "the packet steps are listed");
  check(steps.some((s) => /yourself/.test(s)) && !steps.some((s) => /\bthe owner\b|\bthe claimant\b/i.test(s)), "the steps speak to the owner as you");
  await owner.locator(".state-claim").scrollIntoViewIfNeeded();
  await shot(owner, "03-claim-packet");
  const pdf = await context.request.get(BASE + (await link.getAttribute("href")));
  check(pdf.status() === 200 && (await pdf.body()).subarray(0, 4).toString() === "%PDF", "the claim packet PDF opens");
  facts.packet_steps = steps;
});

// 04. The business's PayPal history: uncollected payouts imported live from PayPal.
await journey("import from PayPal", async () => {
  await open(holder, "#/holder/balances");
  await holder.locator("table.table tbody tr").first().waitFor();
  check((await holder.locator("table.table tbody tr").count()) === 41, "41 balances before the import");
  await holder.getByRole("button", { name: "Import uncollected payouts from PayPal" }).click();
  await holder.getByText(/^PayPal checked \d+ payouts? from /).waitFor({ timeout: 60_000 });
  await holder.waitForTimeout(800);
  const tags = await holder.locator(".tag-paypal").count();
  check(tags >= 3, `three imported payouts carry a From PayPal tag (${tags})`);
  check((await holder.getByText(/^Uncollected in PayPal since \d+ \w+; PayPal returns it on \d+ \w+$/).count()) >= 1, "their state in plain words");
  await holder.locator(".tag-paypal").first().scrollIntoViewIfNeeded();
  await shot(holder, "04-imported-from-paypal");
  await holder.getByRole("button", { name: "Import uncollected payouts from PayPal" }).click();
  await holder.getByText(/all already on this list/).waitFor({ timeout: 60_000 });
  check((await holder.locator(".tag-paypal").count()) === tags, "a second import adds nothing");
  facts.imported = tags;
});

// 05. The business's notice to an owner, drafted by the model in the owner's language. Nothing is sent.
await journey("notice", async () => {
  await open(holder, "#/holder/balances");
  await holder.locator("table.table tbody tr").first().waitFor();
  await shot(holder, "05-holder-balances");
  const row = holder.locator("tr", { hasText: "Tomasz W." });
  await row.getByRole("button", { name: "Draft notice" }).click();
  await holder.getByText("In Polish").waitFor({ timeout: READ_MS });
  await holder.getByText("The same notice in English").waitFor();
  const notice = await holder.locator(".notice").innerText();
  check(/Nothing is sent/.test(notice), "the draft says nothing is sent");
  check(notice.includes(meta.public_url || BASE) || /https?:\/\//.test(notice), "the notice links to Homing's own address");
  check(!/\[[^\]]*Homing[^\]]*\]|\{\{HOMING_LINK\}\}/.test(notice), "no link placeholder left");
  await holder.evaluate(() => window.scrollTo(0, document.querySelector(".notice").getBoundingClientRect().top + window.scrollY - 150));
  await shot(holder, "05-notice-polish");
});

// 06u. Uncollected (paying only): J. R. Okafor's money to a made-up address. PayPal holds it; a new address is refused.
if (PAY) {
  await journey("uncollected", async () => {
    const id = await findAndClaim(owner, { name: "J. R. Okafor", city: "Lagos", country: "Nigeria" }, "between $500 and $1,000", null);
    await upload(owner, ["uncollected/id_card.png", "uncollected/old_statement.png"], null);
    await heading(owner).filter({ hasText: "Checked. Your documents match the record." }).waitFor({ timeout: READ_MS });
    const nobody = `nobody-${Math.random().toString(36).slice(2, 8)}@example.com`;
    await payForm(owner, nobody, "06u");
    await send(owner);
    await owner.getByRole("heading", { name: /^Not collected yet/ }).waitFor({ timeout: PAYPAL_MS });
    check(!(await owner.getByText(/is in your PayPal account/).count()), "nothing says paid");
    await shot(owner, "06u-not-collected");
    await owner.getByRole("button", { name: "Use a different address" }).click();
    const other = `nobody-${Math.random().toString(36).slice(2, 8)}@example.com`;
    await owner.getByPlaceholder(BOX).fill(other);
    await owner.getByRole("button", { name: "Send to this address" }).click();
    await owner.getByRole("heading", { name: /^Held by PayPal/ }).waitFor({ timeout: 60_000 });
    const c = (await api("GET", `/api/claims/${id}`)).body;
    facts.uncollected = { ...confirmation(c), attempt: c.payout.attempt };
    check(c.payout.attempt === 1 && c.payout.state === "UNCLAIMED", `the second send was refused (attempt ${c.payout.attempt}, ${c.payout.state})`);
    check(c.payout.timeline.some((t) => t.title === "Held by PayPal"), "the timeline says Held by PayPal");
    check((await owner.locator(".held").count()) === 0, "no second amber box repeating it");
    await shot(owner, "06u-held-by-paypal");
    const again = await api("POST", `/api/claims/${id}/pay`, { receiver: nobody, receiver_type: "EMAIL" });
    check(again.status === 200 && again.body.payout.attempt === 1, "paying again makes no new payout");
  });
}

// 06. Clean case: Tomasz proves it, is paid, and the business is invoiced the fee from Homing's account.
await journey("clean", async () => {
  const id = await findAndClaim(owner, tomasz, /between \$50 and \$100/i, "06");
  check(/\$86\.40/.test(await textOf(owner)), "the exact amount once the claim is opened");
  check(owner.lastBand === "Very likely yours", `the clean match is very likely (${owner.lastBand})`);
  await upload(owner, ["clean/id_card.png", "clean/old_statement.png"], "06");
  await heading(owner).filter({ hasText: "Checked. Your documents match the record." }).waitFor({ timeout: READ_MS });
  const proofText = await textOf(owner);
  check(/You can send it home now\./.test(proofText) && !/Proven|This money is yours|really you/.test(proofText), "the success wording");
  check(/In English \(the text above is in Polish\)/.test(proofText), "the explanation is in Polish with an English copy");
  check(!/What looked wrong|Notes\b|DEMO DOCUMENT|placeholder|silhouette/i.test(proofText), "no remarks about samples on the owner's page");
  facts.table_top_px = await tableReach(owner);
  await shot(owner, "06-proven");
  await owner.locator(".docs-read").scrollIntoViewIfNeeded();
  await shot(owner, "06-proven-documents");
  await owner.locator(".explain").scrollIntoViewIfNeeded();
  await shot(owner, "06-proven-explanation");
  await payForm(owner, RECEIVER_TOMASZ, "06");
  check((await owner.getByText("PayPal test account").count()) === 1, "the test account chip");
  if (!PAY) return;
  await send(owner);
  const sawSent = await waitPaid(owner, id, "06");
  check(sawSent, "the page showed the payment on its way before Paid");
  const c = await waitInvoice(id);
  facts.tomasz = { ...confirmation(c), invoice: c.invoice };
  check(/^https:\/\/www\.sandbox\.paypal\.com\/invoice\//.test(c.invoice.payer_url), "the invoice links to PayPal");
  await owner.reload();
  await heading(owner).filter({ hasText: "Paid." }).waitFor();
  await shot(owner, "06-paid");
  await owner.getByRole("button", { name: "See PayPal's record" }).click();
  await owner.getByText("This is PayPal's answer, fetched just now").waitFor({ timeout: 30_000 });
  check(/transaction_status\s*SUCCESS/.test(await owner.locator(".paypal-record").innerText()), "PayPal's record says SUCCESS");
  await owner.locator(".paypal-record").scrollIntoViewIfNeeded();
  await shot(owner, "06-paypal-record");
  await shot(owner, "06-paid-full", true);
});

// 07. Partial case: Ana's marriage certificate links her names, but a person must approve, with a written reason.
await journey("partial", async () => {
  const id = await findAndClaim(owner, ana, /between \$100 and \$250/i, "07");
  await upload(owner, ["partial/id_card.png", "partial/utility_bill.png", "partial/marriage_certificate.png"], "07");
  await heading(owner).filter({ hasText: `A person at ${HOLDER} must approve this.` }).waitFor({ timeout: READ_MS });
  check(!(await owner.getByRole("button", { name: "Continue to payment" }).count()), "no payment before approval");
  await shot(owner, "07-needs-a-person");
  await owner.locator(".checks").scrollIntoViewIfNeeded();
  await shot(owner, "07-needs-a-person-checks");
  const bare = await api("POST", `/api/claims/${id}/approve`, { approver: "Claims desk" });
  check(bare.status === 400, "the API refuses an approval without a reason");
  await open(holder, "#/holder/approvals");
  const approve = holder.getByRole("button", { name: "Approve", exact: true });
  await approve.waitFor();
  check(await approve.isDisabled(), "Approve stays off until a reason is written");
  await shot(holder, "07-holder-approvals");
  const reason = "The marriage certificate links Kowalski to Brennan, and the identity card's date of birth matches the record.";
  await holder.getByPlaceholder("Say which documents satisfied you, and why.").fill(reason);
  await shot(holder, "07-holder-reason");
  await approve.click();
  await holder.getByText(/^Approved by Claims desk\./).waitFor();
  await shot(holder, "07-holder-approved");
  // no reload: the owner's page asks again every few seconds
  await owner.getByRole("heading", { name: `Approved by a person at ${HOLDER}.` }).waitFor({ timeout: 20_000 });
  check((await owner.getByText(reason, { exact: false }).count()) >= 1, "the owner sees the reason");
  await owner.evaluate(() => window.scrollTo(0, 0));
  await shot(owner, "07-owner-approved");
  await payForm(owner, RECEIVER_ANA, "07");
  if (!PAY) return;
  await send(owner);
  await waitPaid(owner, id, "07");
  const c = await waitInvoice(id);
  facts.ana = { ...confirmation(c), invoice: c.invoice };
  await owner.reload();
  await heading(owner).filter({ hasText: "Paid." }).waitFor();
  await shot(owner, "07-paid");
});

// 08. Edited identity card: the model sees signs of editing; code rejects it.
await journey("edited", async () => {
  const id = await findAndClaim(owner, { name: "Mohammed Al-Rashid", city: "Dubai", country: "United Arab Emirates" }, /between \$1,000 and \$2,500/i, null);
  await upload(owner, ["edited/id_card.png"], null);
  await heading(owner).filter({ hasText: "We could not confirm this claim." }).waitFor({ timeout: READ_MS });
  const text = await textOf(owner);
  check(text.includes(`${HOLDER} has been told.`) && /2 attempts left\./.test(text), "the fixed sentence and the attempts left");
  check(!/Signs of editing|What looked wrong|What we compared|What was read|typeface|1981/i.test(text), "nothing is revealed to the claimant");
  check(!(await owner.getByRole("button", { name: "Continue to payment" }).count()), "no payment button");
  check((await owner.getByRole("button", { name: "Try other documents" }).count()) === 1, "attempts are offered");
  const c = (await api("GET", `/api/claims/${id}`)).body;
  check(c.status === "REJECTED" && c.proof.attempts_left === 2 && c.proof.checks.length === 0, "rejected, 2 attempts left, nothing revealed");
  await shot(owner, "08-edited-rejected");
  await open(holder, "#/holder/approvals");
  await holder.getByText(/^Turned down by the checks/).waitFor();
  const card = holder.locator("article.review", { hasText: "Mohammed Al-Rashid" });
  check((await card.getByText(/^Signs of editing/).count()) === 1, "the business sees the signs of editing");
  check((await card.getByText("What looked wrong").count()) === 1, "and what looked wrong, in red");
  await card.scrollIntoViewIfNeeded();
  await shot(holder, "08-edited-business-side");
});

// 09. Impostor: a different Wei Zhang claims the Toronto balance with his own card. Rejected; cannot be paid.
await journey("impostor", async () => {
  const id = await findAndClaim(owner, { name: "Wei Zhang", city: "Vancouver", country: "Canada" }, /between \$1,000 and \$2,500/i, null);
  await upload(owner, ["impostor/id_card.png"], null);
  await heading(owner).filter({ hasText: "We could not confirm this claim." }).waitFor({ timeout: READ_MS });
  check(!/1985|Toronto|Mismatch/.test(await textOf(owner)), "no record values and no per-field results");
  check(!(await owner.getByRole("button", { name: "Continue to payment" }).count()), "no payment button");
  await shot(owner, "09-impostor-rejected");
  await open(owner, `#/pay/${id}`);
  await heading(owner).filter({ hasText: "We could not confirm this claim." }).waitFor();
  check(owner.url().includes(`#/prove/${id}`), "the payment page sends a rejected claim back to its result");
  const r = await api("POST", `/api/claims/${id}/pay`, { receiver: "wz.now@example.com", receiver_type: "EMAIL" });
  check(r.status === 409, "the API refuses to pay a rejected claim");
});

// 10. The business's desk at the end: balances, the state filter, invoices, results.
await journey("holder", async () => {
  await open(holder, "#/holder/balances");
  await holder.locator("table.table tbody tr").first().waitFor();
  await holder.getByRole("button", { name: "With the state", exact: true }).click();
  await holder.getByText(/^4 balances,/).waitFor();
  check((await holder.getByText(/^Sent to the state on \d+ \w+ \d{4}$/).count()) === 4, "each shows the date it went to the state");
  await shot(holder, "10-holder-with-the-state");
  await holder.getByRole("tab", { name: "Invoices" }).click();
  if (PAY) {
    await holder.locator("table.table tbody tr").nth(1).waitFor({ timeout: 30_000 });
    const hrefs = await holder.getByRole("link", { name: "Open in PayPal" }).evaluateAll((as) => as.map((a) => a.href));
    check(hrefs.length === 2 && hrefs.every((h) => h.startsWith("https://www.sandbox.paypal.com/invoice/")), "two invoices link to PayPal");
  } else {
    await holder.getByText("No invoices yet.").waitFor();
  }
  await shot(holder, "10-holder-invoices");
  await holder.getByRole("tab", { name: "Results" }).click();
  await holder.getByText("Paid home").waitFor();
  await holder.waitForTimeout(500);
  await shot(holder, "10-holder-results");
  const board = (await api("GET", "/api/scoreboard")).body;
  facts.scoreboard = board;
  check(board.paid === (PAY ? 2 : 0) && board.rejected === 2, `scoreboard counts (${JSON.stringify(board)})`);
  check(!/Claim to paid/.test(await textOf(holder)), "results show the two numbers and counts only");
  await holder.getByRole("tab", { name: /Waiting for approval/ }).click();
  await holder.getByText("Nothing is waiting for you.").waitFor();
  await holder.getByText("Turned down by the checks (2)").waitFor();
  await shot(holder, "10-holder-approvals-empty");
});

await browser.close();
facts.finished = new Date().toISOString();
writeFileSync(join(SHOTS, "run.json"), JSON.stringify({ results, problems, facts, shots }, null, 2));
console.log(JSON.stringify({ results, problems, facts }, null, 2));
process.exit(results.every((r) => r.ok) && problems.length === 0 ? 0 : 1);
