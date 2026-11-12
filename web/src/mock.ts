import type {
  Balance, BalanceStatus, Check, Claim, Claimant, DocumentReading, Explanation, Match, MatchBand, Meta, Notice, PayoutState,
  ProofDocument, ReceiverType, Scoreboard, TimelineEvent, Totals,
} from "./types";

// A small stand-in for the backend, used only when VITE_MOCK=1: a fixture for building the screens offline. The
// screenshots and the video use the real backend. It follows docs/API.md field for field. Add ?scenario=error, ?scenario=empty or ?scenario=slow to the address to see those states.
//
// How it decides, so a demo can be steered:
//   search:    "Tomasz" finds the clean case, "Ana", "Kowalski" or "Brennan" the partial case, "Wei" or "Zhang" two
//              balances under one name, "Mohammed" the balance for the edited sample, "Rosa" or "Delgado" a balance
//              already sent to the state. Anything else: none.
//   documents: a file named with "edited" has signs of editing (REJECTED). A file named with "impostor" is read as
//              the Vancouver Wei Zhang's card: claimed against the Toronto balance, its date of birth and address do
//              not match (REJECTED). Ana's balance is always NEEDS_REVIEW (her address moved to Dublin); a "marriage"
//              file turns her name check into a match. Any other balance: two or more files are APPROVED, one file is
//              NEEDS_REVIEW. A turned-down claim has three attempts in all.
//   payout:    the second refresh after sending settles it. A receiver containing "old" goes UNCLAIMED, anything
//              else is CLAIMED. A new contact is always refused, as the live sandbox refused every cancel: PayPal still
//              holds the first payment.

type Reply = { status: number; body: unknown };

const scenario = new URLSearchParams(window.location.search).get("scenario") ?? "";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const now = () => new Date().toISOString();
const ago = (minutes: number) => new Date(Date.now() - minutes * 60000).toISOString();
const two = (n: number) => n.toFixed(2);
const usd = (n: number | string) => `$${Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const plain = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

const HOLDER = "Larkfield Market";

const META: Meta = {
  holder: { name: HOLDER, kind: "an online marketplace that pays its sellers through PayPal", sample: true },
  demo: true,
  test_receivers: ["sb-43y9ma53877422@personal.example.com", "sb-vgzm153858978@personal.example.com"],
  model_available: true,
};

// The same figures as data/paypal_totals.json.
const TOTALS: Totals = {
  as_of: "2026-11-04",
  source_url: "https://www.sco.ca.gov/upd_download_property_records.html",
  holder_records: 2054035,
  holder_total: "125083033.46",
  bands: [
    { label: "Under $10", records: 1070847, total: "2884678.92" },
    { label: "$10 to $99", records: 752671, total: "24892257.26" },
    { label: "$100 to $999", records: 214589, total: "52786324.68" },
    { label: "$1,000 and over", records: 15928, total: "44519772.60" },
  ],
  over_100: { records: 230517, total: "97306097.28" },
  over_1000: { records: 15928, total: "44519772.60" },
};

interface Person {
  name: string;
  email: string;
  street: string;
  city: string;
  postcode: string;
  country: string;
  dob: string;
}

// The full record behind each balance, as the sample ledger has it. Only masked parts ever leave the "server".
const PEOPLE: Record<string, Person> = {
  B001: { name: "Tomasz Wiśniewski", email: "t.wisniewski@example.com", street: "ul. Długa 12", city: "Kraków", postcode: "31-147", country: "PL", dob: "1979-03-14" },
  B002: { name: "Ana Kowalski", email: "ana.kowalski@example.com", street: "2210 W Cortland St", city: "Chicago", postcode: "60647", country: "US", dob: "1988-11-02" },
  B003: { name: "Wei Zhang", email: "wzhang85@example.com", street: "88 Harbord St", city: "Toronto", postcode: "M5S 1G5", country: "CA", dob: "1985-07-21" },
  B004: { name: "Wei Zhang", email: "zhang.wei.92@example.net", street: "1450 W 7th Ave", city: "Vancouver", postcode: "V6H 1C1", country: "CA", dob: "1992-01-09" },
  B007: { name: "Mohammed Al-Rashid", email: "m.alrashid@example.com", street: "Al Wasl Rd 220", city: "Dubai", postcode: "", country: "AE", dob: "1981-02-25" },
  B038: { name: "Rosa Delgado", email: "rosa.delgado@example.com", street: "1820 N Fresno St", city: "Fresno", postcode: "93703", country: "US", dob: "1971-06-04" },
};

const COUNTRY_NAMES: Record<string, string> = {
  PL: "Poland", US: "United States", CA: "Canada", VN: "Vietnam", NG: "Nigeria", AE: "United Arab Emirates", DE: "Germany",
  ES: "Spain", MX: "Mexico", JP: "Japan", BR: "Brazil", IE: "Ireland", FR: "France", IT: "Italy",
};

function seedLedger(): Balance[] {
  const row = (
    id: string, owner_display: string, last_email_masked: string, last_city: string, country: string,
    amount: string, last_activity: string, status: BalanceStatus, sent_to_state_on: string | null = null,
  ): Balance => ({
    id, owner_display, last_email_masked, last_city, last_country: COUNTRY_NAMES[country] ?? country, amount, currency: "USD",
    last_activity, status, sent_to_state_on,
  });
  return [
    row("B001", "Tomasz W.", "t.***@ex***.com", "Kraków", "PL", "86.40", "2019-06-02", "DORMANT"),
    row("B002", "Ana K.", "an***@ex***.com", "Chicago", "US", "212.75", "2018-02-11", "DORMANT"),
    row("B003", "Wei Z.", "wz***@ex***.com", "Toronto", "CA", "1240.00", "2017-10-19", "DORMANT"),
    row("B004", "Wei Z.", "zh***@ex***.net", "Vancouver", "CA", "57.10", "2020-03-28", "DORMANT"),
    row("B005", "Nguyen V. A.", "an***@ex***.com", "Ho Chi Minh City", "VN", "318.20", "2018-12-01", "PAID"),
    row("B006", "J. R. O.", "jr***@ex***.org", "Lagos", "NG", "640.00", "2016-07-15", "PAID"),
    row("B007", "Mohammed A.", "m.***@ex***.com", "Dubai", "AE", "1875.30", "2019-01-08", "DORMANT"),
    row("B008", "Aleksandr P.", "a.***@ex***.net", "Berlin", "DE", "143.65", "2020-09-30", "UNCLAIMED"),
    row("B010", "María J. G. L.", "mj***@ex***.com", "Madrid", "ES", "410.50", "2017-05-21", "DORMANT"),
    row("B013", "Kenji W.", "k.***@ex***.net", "Osaka", "JP", "1022.40", "2016-11-29", "RETURNED"),
    row("B015", "Lucas O. S.", "lu***@ex***.com", "São Paulo", "BR", "389.75", "2019-11-03", "DORMANT"),
    row("B038", "Rosa D.", "ro***@ex***.com", "Fresno", "US", "348.20", "2015-03-17", "ESCHEATED", "2019-04-15"),
    row("B039", "Linh T.", "li***@ex***.net", "San Jose", "US", "1190.00", "2014-11-05", "ESCHEATED", "2018-04-16"),
    row("B041", "Riverbend Food Bank Cooperative", "of***@ri***.org", "Sacramento", "US", "2210.65", "2014-06-30", "ESCHEATED", "2018-04-16"),
  ];
}

const NO_PAYOUT = (): Claim["payout"] => ({ state: "NONE", batch_id: null, item_id: null, receiver_masked: null, fee: null, timeline: [] });
const EMPTY_PROOF = (): Claim["proof"] => ({
  verdict: "NONE", score: 0, checks: [], flags: [], missing: [], approved_by: null, documents: [], explanation: null,
  review: { required: false, reason: null, approved_by: null, approved_at: null }, attempts: 0, attempts_left: 3,
});

function maskName(name: string): string {
  return name.split(" ").map((w) => w[0] + "·".repeat(Math.max(w.length - 1, 1))).join(" ");
}
function maskEmail(email: string): string {
  const [user, domain] = email.split("@");
  const parts = domain.split(".");
  return `${user.slice(0, 2)}***@${parts[0].slice(0, 2)}***.${parts[parts.length - 1]}`;
}
function mask(receiver: string): string {
  if (receiver.includes("@")) return maskEmail(receiver);
  return receiver.replace(/\d(?=\d{2})/g, "*");
}

// ---- documents --------------------------------------------------------------------------------------------------

type Kind = "id" | "statement" | "bill" | "marriage" | "other";

function kindOf(file: string): Kind {
  const f = plain(file);
  if (/marriage/.test(f)) return "marriage";
  if (/bill|utility/.test(f)) return "bill";
  if (/statement|bank/.test(f)) return "statement";
  if (/id|passport|licen/.test(f)) return "id";
  return "other";
}

const KIND_WORDS: Record<Kind, string> = {
  id: "Identity card",
  statement: "Bank statement",
  bill: "Utility bill",
  marriage: "Marriage certificate",
  other: "Document",
};

function readingFor(file: string, balanceId: string): DocumentReading {
  const f = plain(file);
  const p = PEOPLE[balanceId] ?? PEOPLE.B001;
  const kind = kindOf(file);
  if (/edited/.test(f))
    return { names: [p.name], date_of_birth: p.dob, addresses: [`${p.street}, ${p.city}`], issuer: "Demo Identity Office", dates: [{ label: "Valid until", value: "2031-09-30" }] };
  if (/impostor/.test(f))
    return { names: ["Wei Zhang"], date_of_birth: "1992-01-09", addresses: ["1450 W 7th Ave, Vancouver BC V6H 1C1"], issuer: "Demo Identity Office", dates: [{ label: "Valid until", value: "2032-02-11" }] };
  if (balanceId === "B002") {
    if (kind === "id") return { names: ["Ana Brennan"], date_of_birth: "1988-11-02", issuer: "Demo Identity Office", dates: [{ label: "Expires", value: "2030-08-19" }] };
    if (kind === "bill") return { names: ["Ana Brennan"], addresses: ["14 Pembroke Road, Dublin 4, D04 X2R7, IE"], issuer: "Liffey Demo Energy", dates: [{ label: "Bill date", value: "2026-10-03" }] };
    if (kind === "marriage") return { names: ["Ana Kowalski", "Ana Brennan", "Liam Brennan"], issuer: "Demo Civil Registry", dates: [{ label: "Date of marriage", value: "2019-06-15" }, { label: "Registered", value: "2019-06-17" }] };
  }
  if (kind === "statement" || kind === "bill")
    return { names: [p.name], addresses: [`${p.street}, ${p.postcode} ${p.city}`], emails: [p.email], issuer: "Vistula Demo Savings", dates: [{ label: "Statement date", value: "2019-05-31" }] };
  return { names: [p.name], date_of_birth: p.dob, issuer: "Demo Identity Office", dates: [{ label: "Expires", value: "2031-05-01" }] };
}

function documentsFor(files: File[], balanceId: string): ProofDocument[] {
  return files.map((file) => {
    const edited = /edited/i.test(file.name);
    return {
      file: file.name,
      kind: KIND_WORDS[kindOf(file.name)],
      thumb_url: file.type.startsWith("image/") ? URL.createObjectURL(file) : null,
      read: readingFor(file.name, balanceId),
      concerns: edited
        ? ["The name is in a different typeface from the rest of the card", "The letters of the name sit slightly above the printed line"]
        : [],
      signs_of_editing: edited,
      reading_source: edited ? "SAVED" : "LIVE",
    };
  });
}

function check(field: string, record: string, doc: string, result: Check["result"], note: string): Check {
  return { field, record_value_masked: record, document_value: doc, result, note };
}

interface Decision {
  verdict: Claim["proof"]["verdict"];
  score: number;
  checks: Check[];
  flags: string[];
  missing: string[];
  explanation: Explanation;
}

function decide(files: File[], docs: ProofDocument[], claim: Claim): Decision {
  const id = claim.balance_id;
  const p = PEOPLE[id] ?? PEOPLE.B001;
  const names = (fileOf: (d: ProofDocument) => boolean) => docs.find(fileOf)?.file ?? docs[0]?.file ?? "the document";
  const idFile = names((d) => d.kind === KIND_WORDS.id);
  const lang = id === "B001" ? "pl" : claim.claimant.language || "en";
  const recordName = maskName(p.name);
  const recordDob = `${p.dob.slice(0, 4)}-··-··`;
  const recordCity = `${p.city}, ${p.country}`;
  const recordEmail = maskEmail(p.email);

  const edited = docs.find((d) => d.signs_of_editing);
  if (edited)
    return {
      verdict: "REJECTED", score: 0.1, missing: [],
      flags: [`${edited.file} shows signs of editing`],
      checks: [
        check("name", recordName, p.name, "MATCH", `The name on ${edited.file} is the name on the account.`),
        check("date_of_birth", recordDob, p.dob, "MATCH", `The date of birth on ${edited.file} is the one on the account.`),
        check("address", recordCity, `${p.street}, ${p.city}`, "MATCH", `The address on ${edited.file} is the account's last address.`),
        check("email", recordEmail, "", "MISSING", ""),
        check("document", "", KIND_WORDS.id, "MISMATCH", `${edited.file} shows signs of editing, so it cannot be used.`),
      ],
      explanation: lang === "pl"
        ? {
            language: "pl",
            text: "Dowód osobisty nosi ślady edycji: nazwisko jest napisane inną czcionką niż reszta dokumentu. Dokumentu ze śladami edycji nie można użyć, dlatego roszczenie zostało odrzucone.",
            english: "The identity card shows signs of editing: the name is in a different typeface from the rest of the card. A document with signs of editing cannot be used, so the claim was turned down.",
          }
        : {
            language: "en", text: "The identity card shows signs of editing: the name is in a different typeface from the rest of the card. A document with signs of editing cannot be used, so the claim was turned down.", english: "",
          },
    };

  if (files.some((f) => /impostor/i.test(f.name)))
    return {
      verdict: "REJECTED", score: 0.18, missing: [],
      flags: ["The date of birth on the document is not the one on the account"],
      checks: [
        check("name", recordName, "Wei Zhang", "MATCH", `The name on ${idFile} is the name on the account.`),
        check("date_of_birth", recordDob, "1992-01-09", "MISMATCH", `The date of birth on ${idFile} is not the one on the account.`),
        check("address", recordCity, "1450 W 7th Ave, Vancouver", "MISMATCH", "The address is in another city."),
        check("email", recordEmail, "", "MISSING", ""),
        check("document", "", KIND_WORDS.id, "MATCH", "Read clearly, no signs of editing."),
      ],
      explanation: {
        language: "en",
        text: "The name matches, but the date of birth on the identity card is not the one on the account, and the address is in another city. The claim was turned down.",
        english: "",
      },
    };

  if (id === "B002") {
    const married = docs.some((d) => d.kind === KIND_WORDS.marriage);
    const billFile = names((d) => d.kind === KIND_WORDS.bill);
    return {
      verdict: "NEEDS_REVIEW", score: married ? 0.78 : 0.62,
      flags: [],
      missing: married ? [] : ["A document that links the name Kowalski to the name you use now"],
      checks: [
        married
          ? check("name", recordName, "Ana Brennan, born Ana Kowalski", "MATCH", "The marriage certificate links Kowalski to Brennan.")
          : check("name", recordName, "Ana Brennan", "PARTIAL", "The given name matches; the surname is different."),
        check("date_of_birth", recordDob, p.dob, "MATCH", `The date of birth on ${idFile} is the one on the account.`),
        check("address", recordCity, "14 Pembroke Road, Dublin 4, D04 X2R7, IE", "MISMATCH", `${billFile} shows an address in Dublin. Moved abroad since the account was last used.`),
        check("email", recordEmail, "", "MISSING", "No document shows an email."),
        check("document", "", docs.map((d) => d.kind).join(", "), "MATCH", "All read clearly, no signs of editing."),
      ],
      explanation: {
        language: "en",
        text: married
          ? "The marriage certificate links Ana Kowalski to Ana Brennan, and the date of birth matches. The bill shows an address in Dublin, not the account's last address in Chicago, so a person must decide."
          : "The date of birth matches, but the surname on the documents is Brennan, not Kowalski, and the address is in Dublin. A person must decide.",
        english: "",
      },
    };
  }

  const approved = files.length >= 2;
  const stmtFile = names((d) => d.kind === KIND_WORDS.statement || d.kind === KIND_WORDS.bill);
  return {
    verdict: approved ? "APPROVED" : "NEEDS_REVIEW", score: approved ? 0.96 : 0.66, flags: [],
    missing: approved ? [] : ["A document showing your old address"],
    checks: [
      check("name", recordName, p.name, "MATCH", `The name on ${idFile} is the name on the account.`),
      check("date_of_birth", recordDob, p.dob, "MATCH", `The date of birth on ${idFile} is the one on the account.`),
      approved
        ? check("address", recordCity, `${p.street}, ${p.city}, ${p.postcode}, ${p.country}`, "MATCH", `${stmtFile} shows the account's last address.`)
        : check("address", recordCity, "", "MISSING", "No document shows the old address."),
      approved
        ? check("email", recordEmail, p.email, "MATCH", `${stmtFile} shows the account's last email.`)
        : check("email", recordEmail, "", "MISSING", ""),
      check("document", "", docs.map((d) => d.kind).join(", "), "MATCH", `${docs.length} of ${docs.length} read clearly, no signs of editing. ${docs.map((d) => `${d.file} is marked as a demonstration document.`).join(" ")}`),
    ],
    explanation: lang === "pl"
      ? {
          language: "pl",
          text: approved
            ? "Imię i nazwisko oraz data urodzenia na dowodzie osobistym zgadzają się z kontem. Wyciąg bankowy pokazuje ostatni adres i adres e-mail z konta. Dlatego wypłata została zatwierdzona."
            : "Imię i nazwisko oraz data urodzenia zgadzają się z kontem, ale żaden dokument nie pokazuje dawnego adresu. Dlatego decyzję podejmie człowiek.",
          english: approved
            ? "The name and date of birth on the identity card match the account. The bank statement shows the account's last address and email. That is why the payout was approved."
            : "The name and date of birth match the account, but no document shows the old address. That is why a person will decide.",
        }
      : {
          language: "en",
          text: approved
            ? "The name and date of birth on the identity card match the account. The statement shows the account's last address and email. That is why the payout was approved."
            : "The name and date of birth match the account, but no document shows the old address, so a person will decide.",
          english: "",
        },
  };
}

// ---- claims -----------------------------------------------------------------------------------------------------

function seedClaims(): Claim[] {
  const claimant = (name: string, email: string, country: string): Claimant => ({ name, email, country, language: "en" });
  const paid = (id: string, balance: string, name: string, email: string, country: string, amount: string, inv: string, invStatus: string, mins: number): Claim => {
    const fee = feeOf(amount);
    const batch = `5UXD2E8A${id.slice(-2)}4KQ`;
    const item = `8HG3LZ9P${id.slice(-2)}2RT`;
    const to = maskEmail(email);
    return {
      id, balance_id: balance, status: "PAID", claimant: claimant(name, email, country), amount, currency: "USD",
      proof: { ...EMPTY_PROOF(), verdict: "APPROVED", score: 0.95, attempts: 1, attempts_left: 2 },
      payout: {
        state: "CLAIMED", batch_id: batch, item_id: item, receiver_masked: to, fee: "0.25",
        timeline: [
          { at: ago(mins + 15), state: "PREPARED", title: "Prepared", note: `Payment of ${usd(amount)} prepared for ${to}.`, paypal_id: null },
          { at: ago(mins + 14), state: "SENT", title: "Sent through PayPal", note: `Sent through PayPal Payouts to ${to}. PayPal accepted it.`, paypal_id: batch },
          { at: ago(mins), state: "CLAIMED", title: "Collected", note: `PayPal confirms the money reached ${to}'s PayPal account. Confirmed by PayPal webhook.`, paypal_id: item },
          { at: ago(mins - 1), state: "INVOICE", title: "Fee invoice created", note: `PayPal invoice made out to ${HOLDER} for the ${usd(fee)} fee.`, paypal_id: `INV2-${inv}` },
          { at: ago(mins - 1), state: "INVOICE", title: `Fee invoice sent to ${HOLDER}`, note: `Invoice sent to ${HOLDER} through PayPal. The owner pays nothing.`, paypal_id: `INV2-${inv}` },
        ],
      },
      invoice: { id: `INV2-${inv}`, number: invoiceNumber(inv), amount: fee, status: invStatus, payer_url: `https://www.sandbox.paypal.com/invoice/p/#INV2-${inv}` },
    };
  };
  return [
    paid("c-1990", "B005", "Nguyen Van An", "an.nguyen83@example.com", "Vietnam", "318.20", "0042", "PAID", 190),
    paid("c-1991", "B006", "J. R. Okafor", "jr.okafor@example.org", "Nigeria", "640.00", "0043", "SENT", 75),
  ];
}

let ledger = seedLedger();
let claims = seedClaims();
const refreshes = new Map<string, number>();
const receivers = new Map<string, string>(); // the unmasked receiver of each claim's latest payment
let counter = 3000;
let invoices = 44;

function setBalance(id: string, status: BalanceStatus) {
  ledger = ledger.map((b) => (b.id === id ? { ...b, status } : b));
}

const code = (prefix: string) => `${prefix}${Math.random().toString(36).slice(2, 12).toUpperCase()}`;

function sent(claim: Claim, receiver: string, extra: TimelineEvent[] = []): Claim["payout"] {
  const batch = code("5U");
  const item = code("8H");
  const to = mask(receiver);
  return {
    state: "SENT", batch_id: batch, item_id: item, receiver_masked: to, fee: "0.25",
    timeline: [
      ...claim.payout.timeline,
      ...extra,
      { at: now(), state: "PREPARED", title: "Prepared", note: `Payment of ${usd(claim.amount!)} prepared for ${to}.`, paypal_id: null },
      { at: now(), state: "SENT", title: "Sent through PayPal", note: `Sent through PayPal Payouts to ${to}. PayPal accepted it.`, paypal_id: batch },
    ],
  };
}

function save(next: Claim): Claim {
  claims = claims.map((c) => (c.id === next.id ? next : c));
  return next;
}

const ok = (body: unknown): Reply => ({ status: 200, body });
const fail = (status: number, error: string): Reply => ({ status, body: { error } });

// ---- search -----------------------------------------------------------------------------------------------------

/** The server's amount band: search results never show the exact amount. */
function bandOf(amount: string): string {
  const v = Number(amount);
  const steps = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000];
  const f = (n: number) => `$${n.toLocaleString("en-US")}`;
  if (v < steps[0]) return `under ${f(steps[0])}`;
  for (let i = 0; i < steps.length - 1; i++) if (v < steps[i + 1]) return `between ${f(steps[i])} and ${f(steps[i + 1])}`;
  return `more than ${f(steps[steps.length - 1])}`;
}

/** What the server tells a claimant about a rejected claim: one sentence and the attempts left, no amount. */
function forClaimant(c: Claim): Claim {
  if (c.status !== "REJECTED" && c.proof.verdict !== "REJECTED") return c;
  return {
    ...c,
    amount: null,
    proof: {
      verdict: "REJECTED", score: 0, checks: [], flags: [], missing: [], approved_by: null, documents: [], explanation: null,
      review: { required: false, reason: null, approved_by: null, approved_at: null }, attempts: c.proof.attempts,
      attempts_left: c.proof.attempts_left, demo: c.proof.demo, message: `We could not confirm this claim. ${HOLDER} has been told.`,
    },
  };
}

/** The server's fee: 10% of the balance, at most $25. Invoice numbers are HF- and 16 hex digits. */
const feeOf = (amount: string) => two(Math.min(Number(amount) * 0.1, 25));
const invoiceNumber = (n: string) => `HF-${n.padStart(16, "0").toUpperCase()}`;

function searchFor(name: string): Match[] {
  const q = plain(name);
  const byId = (id: string) => ledger.find((b) => b.id === id)!;
  const m = (id: string, strength: number, band: MatchBand, reasons: string[], doubts: string[]): Match => {
    const b = byId(id);
    return {
      balance_id: id, owner_display: b.owner_display, amount_band: bandOf(b.amount), currency: b.currency, probability: strength / 100,
      strength, band, reasons, doubts, status: b.status,
    };
  };
  if (/tomasz|wisniewski/.test(q))
    return [m("B001", 99, "VERY_LIKELY", ["the name on the account is the name they gave", "an email they gave is the account's last email", "they lived in Kraków, the account's last city"], [])];
  if (/\bana\b|kowalski|brennan/.test(q))
    return [m("B002", 84, "VERY_LIKELY", ["a former name they gave (KOWALSKI) is the name on the account", "they lived in Chicago, the account's last city"], ["none of the emails they gave is the account's last email"])];
  if (/\bwei\b|zhang/.test(q))
    return [
      m("B003", 60, "POSSIBLE", ["the name on the account is the name they gave", "they lived in Toronto, the account's last city"],
        ["another dormant balance carries the same name, in Vancouver; nothing they gave (an email, a street or a postcode) ties this one to them beyond the city"]),
      m("B004", 52, "POSSIBLE", ["the name on the account is the name they gave"],
        ["another dormant balance carries the same name, in Toronto; nothing they gave (an email, a street or a postcode) ties this one to them", "the account's last address is nowhere they mentioned"]),
    ];
  if (/mohammed|rashid/.test(q))
    return [m("B007", 97, "VERY_LIKELY", ["the name on the account is the name they gave", "they lived in Dubai, the account's last city"], [])];
  if (/rosa|delgado/.test(q))
    return [m("B038", 95, "VERY_LIKELY", ["the name on the account is the name they gave", "they lived in Fresno, the account's last city"], [])];
  return [];
}

// ---- notices ----------------------------------------------------------------------------------------------------

function noticeFor(b: Balance): Notice {
  const amount = usd(b.amount);
  const english =
    `Hello,\n\n${HOLDER} is holding a payout of ${amount} from your old seller account. The account was last used on ` +
    `${new Date(`${b.last_activity}T12:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}. ` +
    "If this money is yours, you can collect it through Homing: confirm who you are, and the money goes to your PayPal account. " +
    `Collecting it costs you nothing.\n\nKind regards,\n${HOLDER}`;
  const date = (locale: string) => new Date(`${b.last_activity}T12:00:00`).toLocaleDateString(locale, { day: "numeric", month: "long", year: "numeric" });
  if (b.last_country === "Poland")
    return {
      language: "pl",
      subject: `${HOLDER} ma dla Pana nieodebraną wypłatę`,
      body:
        `Dzień dobry,\n\n${HOLDER} przechowuje wypłatę w wysokości ${b.amount.replace(".", ",")} USD z Pana dawnego konta sprzedawcy. ` +
        `Ostatnia aktywność na koncie była ${date("pl-PL")}. Jeśli to Pana pieniądze, może je Pan odebrać przez Homing: ` +
        "wystarczy potwierdzić tożsamość, a pieniądze trafią na Pana konto PayPal. Odbiór nic Pana nie kosztuje.\n\n" +
        `Z poważaniem,\n${HOLDER}`,
      english,
    };
  if (b.last_country === "Spain" || b.last_country === "Mexico")
    return {
      language: "es",
      subject: `${HOLDER} tiene un pago pendiente para usted`,
      body:
        `Hola:\n\n${HOLDER} guarda un pago de ${b.amount.replace(".", ",")} USD de su antigua cuenta de vendedor. ` +
        `La cuenta se usó por última vez el ${date("es-ES")}. Si este dinero es suyo, puede cobrarlo a través de Homing: ` +
        "confirme su identidad y el dinero llegará a su cuenta de PayPal. Cobrarlo no le cuesta nada.\n\n" +
        `Atentamente,\n${HOLDER}`,
      english,
    };
  return { language: "en", subject: `${HOLDER} is holding a payout for you`, body: english, english: "" };
}

// ---- the handler ------------------------------------------------------------------------------------------------

export async function handle(method: string, fullPath: string, body: unknown): Promise<Reply> {
  const isDocs = fullPath.endsWith("/documents");
  const isNotice = fullPath.endsWith("/notice");
  await wait(scenario === "slow" ? 600000 : isDocs ? 2800 : isNotice ? 1200 : 450);
  if (scenario === "error") return fail(503, "Homing cannot be reached right now. Please try again in a moment.");

  const [path, query = ""] = fullPath.split("?");
  const parts = path.split("/").filter(Boolean); // ["api", ...]
  const find = (id: string) => claims.find((c) => c.id === id);

  if (method === "GET" && path === "/api/meta") return ok(META);
  if (method === "GET" && path === "/api/totals") return ok(TOTALS);

  if (method === "GET" && path === "/api/ledger") {
    if (scenario === "empty") return ok({ balances: [] });
    const status = new URLSearchParams(query).get("status");
    return ok({ balances: status ? ledger.filter((b) => b.status === status) : ledger });
  }

  if (method === "POST" && path === "/api/search") {
    const req = body as { name?: string; former_names?: string[] };
    if (!req?.name?.trim()) return fail(422, "Please tell us your name so we can look.");
    if (scenario === "empty") return ok({ matches: [] });
    return ok({ matches: searchFor([req.name, ...(req.former_names ?? [])].join(" ")) });
  }

  if (method === "POST" && parts[1] === "balances" && parts[3] === "notice") {
    const b = ledger.find((x) => x.id === parts[2]);
    if (!b) return fail(404, "That balance is no longer in the list.");
    if (b.status !== "DORMANT") return fail(409, "Only a balance still waiting for its owner gets a notice.");
    return ok(noticeFor(b));
  }

  if (method === "GET" && path === "/api/claims") return ok({ claims: scenario === "empty" ? [] : claims });

  if (method === "POST" && path === "/api/claims") {
    const req = body as { balance_id: string; claimant: Claimant };
    const balance = ledger.find((b) => b.id === req.balance_id);
    if (!balance) return fail(404, "That balance is no longer in the list.");
    if (balance.status === "ESCHEATED") return fail(409, "This balance is already with the state. Prepare the claim packet instead.");
    const claim: Claim = {
      id: `c-${counter++}`, balance_id: balance.id, status: "OPEN", claimant: req.claimant, amount: balance.amount, currency: balance.currency,
      proof: EMPTY_PROOF(), payout: NO_PAYOUT(), invoice: null,
    };
    claims = [claim, ...claims];
    setBalance(balance.id, "MATCHED");
    return ok(claim);
  }

  if (parts[1] === "claims" && parts[2]) {
    const claim = find(parts[2]);
    if (!claim) return fail(404, "We could not find that claim.");
    const action = parts[3] ?? "";

    if (method === "GET" && !action) return ok(forClaimant(claim));

    if (action === "documents") {
      const files = (body as FormData).getAll("files") as File[];
      if (!files.length) return fail(422, "Please add at least one document.");
      const attempts = (claim.proof.attempts ?? 0) + 1;
      if (claim.proof.verdict === "REJECTED" && (claim.proof.attempts_left ?? 0) <= 0) return fail(409, "This claim has no attempts left.");
      if (claim.proof.verdict === "APPROVED" || claim.proof.approved_by) return fail(409, "This claim is already proven.");
      const docs = [...(claim.proof.verdict === "NEEDS_REVIEW" ? claim.proof.documents ?? [] : []), ...documentsFor(files, claim.balance_id)];
      const allFiles = [...(claim.proof.verdict === "NEEDS_REVIEW" ? (claim.proof.documents ?? []).map((d) => new File([], d.file)) : []), ...files];
      const d = decide(allFiles, docs, claim);
      const nonMatch = d.checks.some((c) => c.result !== "MATCH");
      const proof: Claim["proof"] = {
        verdict: d.verdict, score: d.score, checks: d.checks, flags: d.flags, missing: d.verdict === "REJECTED" ? [] : d.missing,
        approved_by: null, documents: docs, explanation: d.explanation,
        review: { required: d.verdict === "NEEDS_REVIEW" && nonMatch, reason: null, approved_by: null, approved_at: null },
        attempts, attempts_left: Math.max(0, 3 - attempts),
      };
      if (d.verdict === "APPROVED") setBalance(claim.balance_id, "PROVEN");
      if (d.verdict === "REJECTED") proof.explanation = null;
      return ok(forClaimant(save({ ...claim, proof, status: d.verdict === "APPROVED" ? "APPROVED" : d.verdict === "REJECTED" ? "REJECTED" : "PROOF_READY" })));
    }

    if (action === "approve") {
      if (claim.proof.verdict !== "NEEDS_REVIEW") return fail(409, "Only claims waiting for a person can be approved.");
      const req = body as { approver?: string; reason?: string };
      const approver = req?.approver?.trim() || "Claims desk";
      const reason = req?.reason?.trim() || "";
      if (!reason && claim.proof.checks.some((c) => c.result !== "MATCH"))
        return fail(400, "Please say why you are satisfied: not every check is a match.");
      setBalance(claim.balance_id, "PROVEN");
      return ok(save({
        ...claim, status: "APPROVED",
        proof: { ...claim.proof, approved_by: approver, review: { required: Boolean(claim.proof.review?.required), reason: reason || null, approved_by: approver, approved_at: now() } },
      }));
    }

    if (action === "pay") {
      const allowed = claim.status !== "REJECTED" && (claim.proof.verdict === "APPROVED" || Boolean(claim.proof.approved_by));
      if (!allowed) return fail(409, "This claim is not proven yet, so it cannot be paid.");
      if (claim.payout.state !== "NONE") return fail(409, "This claim has already been sent.");
      const req = body as { receiver: string; receiver_type: ReceiverType };
      if (!req.receiver?.trim()) return fail(422, "Please give an email or phone number.");
      refreshes.set(claim.id, 0);
      receivers.set(claim.id, req.receiver.trim());
      setBalance(claim.balance_id, "PAYING");
      return ok(save({ ...claim, status: "PAYING", payout: sent(claim, req.receiver.trim()) }));
    }

    if (action === "refresh") {
      if (claim.payout.state !== "SENT") return ok(claim);
      const n = (refreshes.get(claim.id) ?? 0) + 1;
      refreshes.set(claim.id, n);
      if (n < 2) return ok(claim);
      const to = claim.payout.receiver_masked ?? "the owner";
      const unclaimed = /old/i.test(receivers.get(claim.id) ?? "");
      const state: PayoutState = unclaimed ? "UNCLAIMED" : "CLAIMED";
      const event: TimelineEvent = unclaimed
        ? { at: now(), state, title: "Not collected yet", note: `Not collected yet. ${to} may not have a PayPal account, so PayPal is holding the money and has invited them to collect it.`, paypal_id: claim.payout.item_id }
        : { at: now(), state, title: "Collected", note: `PayPal confirms the money reached ${to}'s PayPal account. Confirmed by asking PayPal.`, paypal_id: claim.payout.item_id };
      const timeline = [...claim.payout.timeline, event];
      const next: Claim = { ...claim, status: unclaimed ? "NEEDS_NEW_CONTACT" : "PAID", payout: { ...claim.payout, state, timeline } };
      if (!unclaimed) {
        const number = String(invoices++).padStart(4, "0");
        const fee = feeOf(claim.amount!);
        next.invoice = { id: `INV2-${number}`, number: invoiceNumber(number), amount: fee, status: "SENT", payer_url: `https://www.sandbox.paypal.com/invoice/p/#INV2-${number}` };
        timeline.push(
          { at: now(), state: "INVOICE", title: "Fee invoice created", note: `PayPal invoice made out to ${HOLDER} for the ${usd(fee)} fee.`, paypal_id: next.invoice.id },
          { at: now(), state: "INVOICE", title: `Fee invoice sent to ${HOLDER}`, note: `Invoice sent to ${HOLDER} through PayPal. The owner pays nothing.`, paypal_id: next.invoice.id },
        );
      }
      setBalance(claim.balance_id, unclaimed ? "UNCLAIMED" : "PAID");
      return ok(save(next));
    }

    if (action === "new-contact") {
      if (claim.payout.state !== "UNCLAIMED" && claim.payout.state !== "RETURNED") return fail(409, "This payment does not need a new contact.");
      const req = body as { receiver: string; receiver_type: ReceiverType };
      if (!req.receiver?.trim()) return fail(422, "Please give an email or phone number.");
      const asked: TimelineEvent = { at: now(), state: "CANCEL_ASKED", title: "Cancel asked", note: "Asked PayPal to cancel the uncollected payment before trying a new contact.", paypal_id: claim.payout.item_id };
      // The live sandbox has refused every cancel of an uncollected payout, so the fixture refuses too.
      const sentence = "Held by PayPal. The first payment has not been collected, so it will not be sent twice. PayPal returns uncollected payments to the sender after 30 days.";
      save({
        ...claim,
        payout: { ...claim.payout, timeline: [...claim.payout.timeline, asked, { at: now(), state: "HELD", title: "Held by PayPal", note: sentence, paypal_id: claim.payout.item_id }] },
      });
      return fail(409, sentence);
    }
  }

  if (method === "GET" && path === "/api/scoreboard") {
    const paid = claims.filter((c) => c.payout.state === "CLAIMED");
    const board: Scoreboard = {
      found: 9 + claims.length, proven: claims.filter((c) => c.proof.verdict === "APPROVED" || c.proof.approved_by).length + 4,
      paid: paid.length, unclaimed: 1, returned: 1, rejected: claims.filter((c) => c.proof.verdict === "REJECTED").length + 1,
      paid_total: two(paid.reduce((s, c) => s + Number(c.amount), 0)),
      fees_invoiced: two(claims.reduce((s, c) => s + Number(c.invoice?.amount ?? 0), 0)),
      median_minutes_to_paid: 14,
    };
    return ok(board);
  }

  if (method === "POST" && parts[1] === "state-claim" && parts[2]) {
    const b = ledger.find((x) => x.id === parts[2]);
    if (!b) return fail(404, "That balance is no longer in the list.");
    if (b.status !== "ESCHEATED") return fail(409, `This balance is still with ${HOLDER}, so its owner can claim it here directly.`);
    return ok({
      pdf_url: `/api/files/state-claim-${b.id}.pdf`,
      steps: [
        `Homing filled in a claim packet with your details and the report number ${HOLDER} gave the state.`,
        "Start the claim on California's website to get the official form, and copy the details from the packet.",
        "Sign the form and add a copy of your ID.",
        "The state checks the claim and pays you directly.",
      ],
    });
  }

  if (method === "POST" && path === "/api/demo/reset") {
    ledger = seedLedger();
    claims = seedClaims();
    refreshes.clear();
    receivers.clear();
    return ok({ ok: true });
  }

  return fail(404, "We could not find that.");
}
