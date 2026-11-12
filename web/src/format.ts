const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export const count = (n: number) => whole.format(Math.round(n));

/** "$11,478.09" from the API's "11478.09". Other currencies keep their code: "1,975.40 CAD". */
export function money(amount: string | number, currency = "USD", cents = true): string {
  const n = Number(amount);
  const digits = { minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: cents ? 2 : 0 };
  if (currency === "USD") return new Intl.NumberFormat("en-US", { style: "currency", currency, ...digits }).format(n);
  return `${new Intl.NumberFormat("en-US", digits).format(n)} ${currency}`;
}

/** "$97.4 million" for short labels. */
export function shortMoney(amount: string | number): string {
  const n = Number(amount);
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)} million`;
  if (n >= 1e3) return `$${Math.round(n / 1e3)} thousand`;
  return money(n, "USD", false);
}

/** "14 March 1979" from "1979-03-14". */
export function longDate(iso: string): string {
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

/** "14 Mar 2019". */
export function shortDate(iso: string): string {
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** Every ISO date inside a line of text becomes "14 March 1979". A masked "1979-··-··" says what is hidden. */
export function datesIn(text: string): string {
  return text
    .replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (m) => longDate(m))
    .replace(/\b(\d{4})-[·*•]{2}-[·*•]{2}/g, (_, y: string) => `${y}, day and month hidden`);
}

export function clock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const BANDS = {
  VERY_LIKELY: { words: "Very likely yours", tone: "strong" },
  POSSIBLE: { words: "Possibly yours", tone: "middle" },
  UNLIKELY: { words: "Probably someone else", tone: "weak" },
} as const;

/** Match strength out of 100 and its band. Older servers send only "probability"; it is read the same way. */
export function strengthOf(m: { strength?: number; band?: string; probability: number }) {
  const value = typeof m.strength === "number" ? Math.round(m.strength) : Math.round(m.probability * 100);
  const band = (m.band && m.band in BANDS ? m.band : value >= 80 ? "VERY_LIKELY" : value >= 40 ? "POSSIBLE" : "UNLIKELY") as keyof typeof BANDS;
  return { value, band, ...BANDS[band] };
}

export function sentence(line: string): string {
  const t = line.trim();
  if (!t) return t;
  // A line that starts with a file name ("old_statement.png shows ...") keeps the name as it is.
  const starts = t.split(/\s/)[0];
  const first = /[._]\w/.test(starts) ? t : t.charAt(0).toUpperCase() + t.slice(1);
  return /[.!?]$/.test(first) ? first : `${first}.`;
}

const FIELDS: Record<string, string> = {
  name: "Name",
  address: "Address",
  email: "Email",
  date_of_birth: "Date of birth",
  document: "Document",
};
export const fieldName = (f: string) => FIELDS[f] ?? sentence(f.replace(/_/g, " ")).replace(/\.$/, "");

export const RESULT_WORDS: Record<string, string> = { MATCH: "Match", PARTIAL: "Partial", MISMATCH: "Mismatch", MISSING: "Missing" };

export const BALANCE_WORDS: Record<string, string> = {
  DORMANT: "Waiting for owner",
  MATCHED: "Owner found",
  PROVEN: "Checked",
  PAYING: "Paying",
  PAID: "Paid",
  UNCLAIMED: "Not collected",
  RETURNED: "Returned by PayPal",
  ESCHEATED: "With the state",
};

export const PAYOUT_WORDS: Record<string, string> = {
  PREPARED: "Prepared",
  INVOICE: "Fee invoice",
  REFUSED: "Not accepted",
  CANCEL_ASKED: "Cancel asked",
  HELD: "Held by PayPal",
  INVOICE_LATER: "Invoice to follow",
  SENT: "Sent through PayPal",
  CLAIMED: "Collected",
  UNCLAIMED: "Not collected yet",
  RETURNED: "Returned by PayPal",
  FAILED: "Not delivered",
  CANCELLED: "Cancelled",
  NONE: "Not sent",
};

export function receiverType(value: string): "EMAIL" | "PHONE" {
  return value.includes("@") ? "EMAIL" : "PHONE";
}

export function validReceiver(value: string): boolean {
  const v = value.trim();
  if (v.includes("@")) return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
  return /^\+?[\d\s().-]{7,}$/.test(v);
}

/** Phones go to PayPal as digits with a leading +. */
export function cleanReceiver(value: string): string {
  const v = value.trim();
  if (v.includes("@")) return v;
  const digits = v.replace(/[^\d]/g, "");
  return `+${digits}`;
}

export const canPay = (proof: { verdict: string; approved_by: string | null }, status?: string) =>
  status !== "REJECTED" && proof.verdict !== "REJECTED" && (proof.verdict === "APPROVED" || Boolean(proof.approved_by));

/** Notes that only say a document is a sample are dropped: one banner at the top of the app says it once. */
export function withoutDemoNotes(note: string): string {
  return note
    .split(/(?<=[.!?])\s+/)
    .filter((part) => !isDemoNote(part))
    .join(" ")
    .trim();
}

const LANGUAGES: Record<string, string> = {
  en: "English", pl: "Polish", es: "Spanish", pt: "Portuguese", fr: "French", de: "German", it: "Italian",
  vi: "Vietnamese", zh: "Chinese", ja: "Japanese", ko: "Korean", yo: "Yoruba", tl: "Tagalog", nl: "Dutch", uk: "Ukrainian",
  ar: "Arabic", cs: "Czech", tr: "Turkish", sv: "Swedish", nb: "Norwegian", da: "Danish", fi: "Finnish", ru: "Russian",
  el: "Greek", ro: "Romanian", hu: "Hungarian", th: "Thai", id: "Indonesian", ms: "Malay", he: "Hebrew", fa: "Persian", bn: "Bengali",
};

/** The language the claimant most likely reads, from the country they typed (the same guess the server makes for
 * notices). A browser set to a language other than English wins; otherwise the country decides. */
const COUNTRY_LANGUAGE: Record<string, string> = {
  poland: "pl", pl: "pl", polska: "pl", mexico: "es", mx: "es", spain: "es", es: "es", argentina: "es", colombia: "es",
  chile: "es", peru: "es", france: "fr", fr: "fr", germany: "de", de: "de", austria: "de", brazil: "pt", br: "pt",
  portugal: "pt", pt: "pt", italy: "it", it: "it", vietnam: "vi", "viet nam": "vi", vn: "vi", japan: "ja", jp: "ja",
  china: "zh", cn: "zh", "south korea": "ko", korea: "ko", kr: "ko", ukraine: "uk", ua: "uk", netherlands: "nl", nl: "nl",
  "united arab emirates": "ar", uae: "ar", ae: "ar", "saudi arabia": "ar", egypt: "ar", morocco: "fr", turkey: "tr",
  "czech republic": "cs", czechia: "cs", sweden: "sv", russia: "ru", greece: "el", romania: "ro", hungary: "hu",
};
export function languageFor(country: string, browser: string): string {
  const b = (browser || "en").slice(0, 2).toLowerCase();
  if (b !== "en") return b;
  return COUNTRY_LANGUAGE[country.trim().toLowerCase()] ?? "en";
}
export const languageName = (code: string) => LANGUAGES[code?.slice(0, 2).toLowerCase()] ?? code;
export const isEnglish = (code: string) => !code || code.slice(0, 2).toLowerCase() === "en";

/** Match reasons are written about "they"; the owner reads them about themselves. "(TOMASZ)" becomes "(Tomasz)". */
export function toYou(line: string): string {
  // "another dormant balance carries the same name, in Vancouver; nothing they gave (an email, a street or a
  // postcode) ties this one to them beyond the city" -> two sentences about "you", keeping the last words.
  const twin = line.match(/another (?:dormant )?balance (?:carries the same name|has this name),? in ([^;.]+?)[;.]\s*nothing they gave(?: \(([^)]*)\))? ties this one to them(.*)$/i);
  if (twin) {
    const what = twin[2] ? ` (${twin[2].trim()})` : "";
    const where = twin[1].trim() === "another city" ? "in another city" : `in ${twin[1].trim()}`;
    return `Another balance has this name, ${where}. Nothing you gave${what} ties this one to you${twin[3].replace(/[.\s]+$/, "")}.`;
  }
  return sentence(
    line
      .replace(/\bthey gave\b/g, "you gave")
      .replace(/\bthey lived\b/g, "you lived")
      .replace(/\bthey mentioned\b/g, "you mentioned")
      .replace(/\bthey say\b/g, "you say")
      .replace(/\bties this one to them\b/g, "ties this one to you")
      .replace(/\bties it to them\b/g, "ties it to you")
      .replace(/\bone they\b/g, "one you")
      .replace(/\(([A-ZÀ-Þ][A-ZÀ-Þ' -]+)\)/g, (_, w: string) => `(${w.toLowerCase().replace(/(^|[ '-])(\p{L})/gu, (m) => m.toUpperCase())})`),
  );
}

/** A remark that only says a document is a sample ("The footer reads 'DEMO DOCUMENT ...'"). */
export const isDemoNote = (text: string) =>
  /demo document|demonstration document|invented person|not valid for any purpose|marked as a (demo|sample|specimen)|marked sample/i.test(text);

/** "Identity card (demo)" -> "Identity card": the banner says once that every document is a sample. */
export const withoutDemoWord = (kind: string) => kind.replace(/\s*\([^()]*(demo|sample|specimen)[^()]*\)\s*$/i, "").trim();
