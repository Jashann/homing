# Public guard (must be in the server before PayPal keys are attached to the public demo)

The hosted demo is open to anyone, and anyone can open the business's desk, approve a claim and press Pay. Until the
guard below is in `src/homing/server.py`, the service runs **without** the PayPal secrets, so every pay attempt fails
closed with HTTP 503. Everything here applies only when `HOMING_PUBLIC=1` (already set on the Cloud Run service);
local runs and the tests behave as now.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `HOMING_PUBLIC` | unset | `1` turns every rule below on |
| `HOMING_PUBLIC_DAILY_CAP` | `300` | Most USD the server may send in PayPal payouts per UTC day |
| `HOMING_PUBLIC_MAX_UPLOAD_MB` | `5` | Largest single uploaded file in public mode |
| `HOMING_PUBLIC_RESET_SECONDS` | `60` | Shortest gap between two "Start again" resets |

Read them once in `create_app()` next to `demo`, e.g. `public = os.environ.get("HOMING_PUBLIC", "").strip() == "1"`.

## 1. Payouts only to sandbox test addresses

Applies to every route that sends money: `POST /api/claims/{id}/pay` and `POST /api/claims/{id}/new-contact`
(it sends the held payment to a new receiver). Check before anything is written or sent to PayPal:

- `receiver_type` must be `EMAIL`. A `PHONE` receiver is refused.
- The receiver must contain exactly one `@`, and its domain (`receiver.strip().lower().rsplit("@", 1)[1]`) must be
  exactly `personal.example.com` or `example.com`. Compare the whole domain, not a substring or an `endswith` on the
  full string.
- Refusal: HTTP 403, `{"error": "In this public demo, payments go only to PayPal sandbox test addresses ending in
  @personal.example.com or @example.com."}`

## 2. Daily cap on money sent

- Before sending, add up the amounts of every payout the server has sent today (UTC) and add the amount about to be
  sent. Count each payout attempt that reached PayPal (any state after "sent", including UNCLAIMED, held and failed
  after sending), not only collected ones. Source of truth: the settlement database (`Settlement`, `data/settlement.sqlite`),
  which "Start again" does not clear. Add a small method there, e.g. `Settlement.sent_since(utc_midnight) -> Decimal`,
  summing `claims.amount` once per row in `attempts` whose send was recorded in `audit` (its `at`) since midnight,
  rather than counting Homing's own claims table in `store.py` (a reset clears it, which would reset the cap).
- Non-USD amounts: refuse in public mode (the sample ledger is all USD), so the cap is one sum.
- If the total would go over `HOMING_PUBLIC_DAILY_CAP`, refuse with HTTP 429:
  `{"error": "This public demo has sent its limit of $300.00 in sandbox payouts today. Paying opens again at midnight UTC. Everything else still works."}`
  (format the cap from the setting).
- The check and the send must not race: hold one `threading.Lock` around "sum, compare, create the payout record"
  so two pay clicks at the same moment cannot both pass. Cloud Run runs at most one instance (`--max-instances 1`), so
  a process lock is enough.
- Known gap: the container's disk is wiped when the instance stops (after about 15 idle minutes, or a redeploy), which
  also resets the day's total. Worst case is the cap once per instance start. That is acceptable for sandbox money;
  say so in the README if the demo is advertised.

## 3. "Start again" rate limit

`POST /api/demo/reset` reloads the ledger and clears every claim, for every visitor at once (they share one server).

- At most one reset per `HOMING_PUBLIC_RESET_SECONDS` (default 60) for the whole server, and at most 10 per hour from
  one client address. On Cloud Run the client address is the first entry of `X-Forwarded-For`
  (`request.headers.get("x-forwarded-for", "").split(",")[0].strip()`, falling back to `request.client.host`).
- Refusal: HTTP 429, `{"error": "The demo was just started again. Please wait a minute and try again."}`
- Keep the counters in memory (a dict of deques); they need not survive a restart.

## 4. Upload limits

In `POST /api/claims/{id}/documents`:

- Refuse before parsing the form when `Content-Length` is over `8 * MAX_UPLOAD + 1 MB` (HTTP 413), so a huge body is
  never read into memory.
- Each file at most `HOMING_PUBLIC_MAX_UPLOAD_MB` (default 5 MB) instead of 15 MB; at most 4 files per request
  (currently 8).
- At most 12 stored documents per claim, and at most 200 MB under `data/files/` in total. Cloud Run's disk is held
  in the instance's memory (1 GiB here), so a full disk would crash the server. When the total is reached, refuse with
  HTTP 507 `{"error": "This public demo is full right now. Press Start again on the business's view, or try later."}`
  and make "Start again" delete `data/files/claims/*` and `data/files/state-claims/*` in public mode.
- Messages stay plain, in the style of the existing ones ("id_card.png is larger than 5 MB.").

## 5. Smaller points

- In public mode the 503 text "add sandbox keys to ~/.paypal_sandbox and restart it" should read "Paying is turned
  off in this public demo." (it is what visitors see until the keys are attached).
- If a model key is ever added to the service (`ANTHROPIC_API_KEY`), document reading costs money per upload: add a
  per-address limit on document uploads (e.g. 20 per hour) at the same time.
- No other route sends money or mail. `POST /api/import/paypal` only reads PayPal and adds the three seeded balances once.

## Tests to add (tests/test_public_guard.py, PayPal faked as in the existing tests)

1. Public mode: pay to `sb-x@personal.example.com` and `nobody@example.com` is sent; `a@gmail.com`,
   `a@example.com.evil.org`, `a@notexample.com` and a PHONE receiver are refused with 403 and nothing reaches the fake.
2. Cap 300: $212.75 then $86.40 are sent ($299.15); a third payout of any size is refused with 429; after a
   "Start again" it is still refused; a fake clock past UTC midnight lets it through.
3. Two concurrent pays of $200 with cap 300: exactly one is sent.
4. Reset twice within 60 s: the second gets 429.
5. A 6 MB file is refused with 413; 5 files in one request are refused; the total-bytes limit gives 507.
6. `HOMING_PUBLIC` unset: none of the above applies (existing tests keep passing).

## When it is in

Redeploy the final code and attach the secrets in one go:

```bash
deploy/deploy.sh /path/to/homing --with-paypal
```

or, without rebuilding, attach them to the running service:

```bash
gcloud run services update homing --project "$GOOGLE_CLOUD_PROJECT" --region us-central1 \
  --update-secrets PAYPAL_CLIENT_ID=homing-paypal-client-id:latest,PAYPAL_SECRET=homing-paypal-secret:latest,PAYPAL_INVOICER_CLIENT_ID=homing-paypal-invoicer-client-id:latest,PAYPAL_INVOICER_SECRET=homing-paypal-invoicer-secret:latest
```

Then check from outside that a payout to `a@gmail.com` is refused and one to the sandbox personal account goes through.
