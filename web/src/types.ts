// Shapes of the API, exactly as docs/API.md describes them. Money is always a string with two decimals.

export interface Band {
  label: string;
  records: number;
  total: string;
}

export interface Meta {
  holder: { name: string; kind: string; sample: boolean };
  demo: boolean;
  test_receivers: string[];
  model_available: boolean;
  public?: boolean;
}

export interface Totals {
  as_of: string;
  source_url: string;
  holder_records: number;
  holder_total: string;
  bands: Band[];
  over_100: { records: number; total: string };
  over_1000: { records: number; total: string };
  property_types?: Band[];
  what_is_counted?: string;
}

export type BalanceStatus = "DORMANT" | "MATCHED" | "PROVEN" | "PAYING" | "PAID" | "UNCLAIMED" | "RETURNED" | "ESCHEATED";

export interface Balance {
  id: string;
  owner_display: string;
  last_email_masked: string;
  last_city: string;
  last_country: string;
  amount: string;
  currency: string;
  last_activity: string;
  status: BalanceStatus;
  /** ISO date the balance was handed to the state (ESCHEATED only, else null). */
  sent_to_state_on?: string | null;
  /** "Ledger" for the business's own report, "PayPal Payouts" for an uncollected payout imported from PayPal. */
  source?: string;
  paypal?: PayPalHold | null;
}

/** An uncollected payout found in the business's PayPal payout history, as PayPal reported it. */
export interface PayPalHold {
  item_id: string;
  batch_id: string;
  status: string;
  since: string | null;
  returns_on: string | null;
  /** The state in plain words, written by the server. */
  words: string | null;
}

export interface ImportResult {
  batches_checked: number;
  items_checked: number;
  added: number;
  updated: number;
  balances: Balance[];
}

export interface PayPalRecord {
  endpoint: string;
  fetched_at: string;
  record: {
    transaction_status: string | null;
    payout_item_id: string | null;
    payout_batch_id: string | null;
    transaction_id: string | null;
    time_processed: string | null;
    amount: { value: string; currency: string } | null;
    payout_item_fee: { value: string; currency: string } | null;
  };
}

export interface Place {
  city: string;
  country: string;
}

export interface SearchRequest {
  name: string;
  former_names: string[];
  emails: string[];
  places: Place[];
}

export type MatchBand = "VERY_LIKELY" | "POSSIBLE" | "UNLIKELY";

export interface Match {
  balance_id: string;
  owner_display: string;
  /** Before a claim is opened the amount is only a band ("between $50 and $100"). */
  amount_band: string;
  currency: string;
  /** The balance's last city, only when the searcher typed that city. */
  city?: string | null;
  probability: number;
  /** Version 2: a 0-100 heuristic and its band. Never shown as a probability. */
  strength?: number;
  band?: MatchBand;
  reasons: string[];
  doubts: string[];
  status: BalanceStatus;
  sent_to_state_on?: string | null;
}

export interface Claimant {
  name: string;
  email: string;
  country: string;
  language: string;
  former_names?: string[];
}

export type CheckResult = "MATCH" | "PARTIAL" | "MISMATCH" | "MISSING";

export interface Check {
  field: string;
  record_value_masked: string;
  document_value: string;
  result: CheckResult;
  note: string;
}

export type Verdict = "APPROVED" | "NEEDS_REVIEW" | "REJECTED" | "NONE";

export interface DocumentReading {
  names?: string[];
  date_of_birth?: string | null;
  addresses?: string[];
  emails?: string[];
  issuer?: string | null;
  dates?: { label: string; value: string }[];
}

export interface ProofDocument {
  file: string;
  kind: string;
  thumb_url?: string | null;
  read: DocumentReading;
  concerns: string[];
  signs_of_editing: boolean;
  reading_source: "LIVE" | "SAVED";
  /** "read" when the model read it; otherwise "problem" says why not, in a plain sentence. */
  status?: string;
  problem?: string | null;
  summary?: string;
  marked_sample?: boolean;
}

export type TextSource = "LIVE" | "SAVED" | "PLAIN";

export interface Explanation {
  language: string;
  text: string;
  english: string;
  source?: TextSource;
}

export interface Review {
  required: boolean;
  reason: string | null;
  approved_by: string | null;
  approved_at: string | null;
}

export interface Proof {
  verdict: Verdict;
  score: number;
  checks: Check[];
  flags: string[];
  missing: string[];
  approved_by: string | null;
  documents?: ProofDocument[];
  explanation?: Explanation | null;
  review?: Review | null;
  attempts?: number;
  attempts_left?: number;
  /** True when sample-stamped documents were accepted because the server runs in demo mode. */
  demo?: boolean;
  /** For a rejected claim, the only thing the claimant is told. */
  message?: string | null;
}

export type PayoutState = "NONE" | "SENT" | "CLAIMED" | "UNCLAIMED" | "RETURNED" | "FAILED" | "CANCELLED";

export interface TimelineEvent {
  at: string;
  state: string;
  /** Version 2: a plain title written by the server ("Sent through PayPal", "Held by PayPal", ...). */
  title?: string;
  note: string;
  paypal_id: string | null;
  /** How PayPal told us: a verified webhook delivery, or Homing asking PayPal. */
  heard_by?: "WEBHOOK" | "POLLING" | null;
}

export interface Payout {
  state: PayoutState;
  batch_id: string | null;
  item_id: string | null;
  receiver_masked: string | null;
  fee: string | null;
  attempt?: number;
  timeline: TimelineEvent[];
  /** Which one confirmed the final state of the payout. */
  confirmed_by?: "WEBHOOK" | "POLLING" | null;
}

export interface Invoice {
  id: string;
  number: string;
  amount: string;
  status: string;
  payer_url: string;
}

export type ClaimStatus = "OPEN" | "PROOF_READY" | "APPROVED" | "PAYING" | "PAID" | "NEEDS_NEW_CONTACT" | "REJECTED";

export interface Claim {
  id: string;
  payout_address?: string | null;
  balance_id: string;
  status: ClaimStatus;
  claimant: Claimant;
  /** null on the claimant's side of a rejected claim: the exact balance is not shown. */
  amount: string | null;
  currency: string;
  proof: Proof;
  payout: Payout;
  invoice: Invoice | null;
  source?: string;
  /** Set while PayPal still holds this balance's earlier payout as uncollected: it cannot be paid until returned. */
  paypal_held?: string | null;
}

export type ReceiverType = "EMAIL" | "PHONE";

export interface Scoreboard {
  found: number;
  proven: number;
  paid: number;
  unclaimed: number;
  returned: number;
  rejected: number;
  paid_total: string;
  fees_invoiced: string;
  median_minutes_to_paid: number;
}

export interface StateClaim {
  pdf_url: string;
  steps: string[];
}

export interface Notice {
  language: string;
  subject: string;
  body: string;
  english: string;
  english_subject?: string;
  source?: TextSource;
}

export interface AgentStep {
  n: number;
  say: string;
  tool: string | null;
  args: Record<string, unknown> | null;
  result: string;
  outcome: "running" | "allowed" | "refused" | "error";
  started_at: string;
  ended_at: string | null;
}

export interface AgentTask {
  kind: string;
  group: string;
  title: string;
  balance_id?: string | null;
  claim_id?: string | null;
  steps: AgentStep[];
  status: "waiting" | "running" | "done" | "stopped" | "error";
  summary: string | null;
}

export interface AgentSummary {
  found: number;
  held: number;
  notices: number;
  sent_to_person: number;
  paid: number;
  pay_sent: number;
  refused: number;
  tool_calls: number;
}

export interface AgentRun {
  id: string;
  mode: "queue" | "brakes";
  label: string;
  rehearsal?: boolean;
  status: "running" | "done";
  started_at: string;
  ended_at: string | null;
  tasks: AgentTask[];
  summary: AgentSummary | null;
  note?: string;
}
