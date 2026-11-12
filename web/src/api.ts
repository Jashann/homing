import type {
  AgentRun, Balance, BalanceStatus, Claim, Claimant, ImportResult, Match, Meta, Notice, PayPalRecord, ReceiverType, Scoreboard, SearchRequest,
  StateClaim, Totals,
} from "./types";

// Every call the interface makes, written to docs/API.md. With VITE_MOCK=1 the same calls are answered by
// src/mock.ts, a fixture for building the screens offline. Screenshots and the video use the real backend.

export const MOCK = import.meta.env.VITE_MOCK === "1";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

const UNREACHABLE = "Homing cannot be reached right now. Please try again in a moment.";

function fallback(status: number): string {
  if (status === 404) return "We could not find that. It may have been closed or removed.";
  if (status === 409) return "This step is not open yet. Finish the step before it first.";
  if (status === 422 || status === 400) return "Some of the details could not be read. Please check them and try again.";
  return "Something went wrong on our side. Please try again in a moment.";
}

async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  if (import.meta.env.VITE_MOCK === "1") {
    const { handle } = await import("./mock");
    const reply = await handle(method, path, body);
    if (reply.status >= 400) throw new ApiError((reply.body as { error?: string })?.error || fallback(reply.status), reply.status);
    return reply.body as T;
  }
  const init: RequestInit = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(UNREACHABLE, 0);
  }
  if (response.ok) return (await response.json()) as T;
  let message = "";
  try {
    const data = await response.json();
    if (typeof data?.error === "string" && data.error.trim()) message = data.error;
  } catch {
    // Not JSON (a proxy or the network): a general sentence is used instead.
  }
  if (!message) message = response.status >= 502 && response.status <= 504 ? UNREACHABLE : fallback(response.status);
  throw new ApiError(message, response.status);
}

export const getMeta = () => request<Meta>("GET", "/api/meta");
export const draftNotice = (balanceId: string) => request<Notice>("POST", `/api/balances/${balanceId}/notice`);
export const getTotals = () => request<Totals>("GET", "/api/totals");
export const getLedger = (status?: BalanceStatus) =>
  request<{ balances: Balance[] }>("GET", status ? `/api/ledger?status=${status}` : "/api/ledger");
export const search = (body: SearchRequest) => request<{ matches: Match[] }>("POST", "/api/search", body);
export const createClaim = (balance_id: string, claimant: Claimant) => request<Claim>("POST", "/api/claims", { balance_id, claimant });
export const uploadDocuments = (id: string, files: File[]) => {
  const form = new FormData();
  files.forEach((f) => form.append("files", f, f.name));
  return request<Claim>("POST", `/api/claims/${id}/documents`, form);
};
export const approveClaim = (id: string, approver: string, reason: string) =>
  request<Claim>("POST", `/api/claims/${id}/approve`, { approver, reason });
export const payClaim = (id: string, receiver: string, receiver_type: ReceiverType, currency: string) =>
  request<Claim>("POST", `/api/claims/${id}/pay`, { receiver, receiver_type, currency });
export const refreshClaim = (id: string) => request<Claim>("POST", `/api/claims/${id}/refresh`);
export const newContact = (id: string, receiver: string, receiver_type: ReceiverType) =>
  request<Claim>("POST", `/api/claims/${id}/new-contact`, { receiver, receiver_type });
export const getClaim = (id: string) => request<Claim>("GET", `/api/claims/${id}`);
export const getClaims = () => request<{ claims: Claim[] }>("GET", "/api/claims");
export const getScoreboard = () => request<Scoreboard>("GET", "/api/scoreboard");
export const prepareStateClaim = (balanceId: string) => request<StateClaim>("POST", `/api/state-claim/${balanceId}`);
export const importFromPayPal = () => request<ImportResult>("POST", "/api/import/paypal");
export const getPayPalRecord = (id: string) => request<PayPalRecord>("GET", `/api/claims/${id}/paypal-record`);
export const resetDemo = () => request<{ ok: boolean }>("POST", "/api/demo/reset");
export const leavePayoutAddress = (id: string, receiver: string) => request<Claim>("POST", `/api/claims/${id}/payout-address`, { receiver });
export const startAgent = (mode: "queue" | "brakes", dry_pay = false) => request<AgentRun>("POST", "/api/agent/runs", { mode, dry_pay });
export const getAgentRun = (id: string) => request<AgentRun>("GET", `/api/agent/runs/${id}`);
export const getSavedAgentRuns = () => request<{ label: string; saved_at?: string; queue?: AgentRun; brakes?: AgentRun }>("GET", "/api/agent/saved");
