# Homing

Homing helps a business pay uncollected money back to its owners through PayPal before the money has to be handed to the state as unclaimed property. Its AI desk agent works the business's queue and can pay through PayPal, but only code, or a person who must write down why, can approve a payment. Homing finds the owner of a forgotten payout, checks their documents against the account record, and pays it home with PayPal Payouts. For money the state already holds, it prepares a claim packet for the owner to review, sign and send.

The business in this demo is **Larkfield Market**, an invented online marketplace that pays its sellers through PayPal. PayPal is where the problem starts as well as where it is fixed: a seller payout sent to an address nobody uses sits in PayPal as UNCLAIMED and goes back to the business after 30 days. Homing asks PayPal about each payout batch the business has on file (in the demo, three seeded sandbox batches in `data/payout_history.json`), adds every item PayPal reports `UNCLAIMED` or `RETURNED`, finds the owners, and pays them.

## The number

California's public unclaimed property file (dated 4 November 2026) lists **2,054,035 records that PayPal reported to California as unclaimed property, worth $125,083,033**. These are records (one line of the state's file each), not people or accounts.

| Size of record | Records | Total |
|---|---:|---:|
| Under $10 | 1,070,847 | $2.9 million |
| $10 to $99 | 752,671 | $24.9 million |
| $100 to $999 | 214,589 | $52.8 million |
| $1,000 and over | 15,928 | $44.5 million |

By the state's property type: 1,918,703 records (93.4%, $115.7 million) are "miscellaneous intangible property", 127,234 (6.2%, $8.1 million) are commissions, and 8,098 ($1.2 million) are other types: mostly codes outside the standard list (5,172 records), plus customer overpayments, payments for goods and services, vendor checks and wages. The full split is in `data/paypal_totals.json`.

PayPal is one holder among many. Across every holder in the same file, the property types that are payments a business owed a person (wages, commissions, vendor and accounts payable checks, refunds and customer credits, royalties, expense and other payee checks) come to **44,698,123 records worth $3,044,769,901**: amounts businesses of every kind handed to California for payments they owed to people and did not deliver. The three largest types are refunds due ($476.5 million), miscellaneous outstanding checks ($427.6 million) and accounts payable ($411.8 million). Each included code, with the state's description, is in `data/payee_totals.json`, reproduced with `uv run python scripts/payee_totals.py`. Homing touches none of this money: it works on the step before, while the business still holds the payment.

Source: the California State Controller's bulk files (`01_From_0_To_Below_10.zip` to `04_From_500_To_Beyond.zip` at https://claimit.ca.gov/upd-property-records/, listed on https://www.sco.ca.gov/upd_download_property_records.html). `scripts/paypal_totals.py` loads them into a local DuckDB file and sums `CURRENT_CASH_BALANCE` where the reporting business (`HOLDER_NAME`) contains `PAYPAL` (PAYPAL 1,088,739 records, PAYPAL INC 965,245, PAYPAL HOLDINGS INC 50, EBAY INC/PAYPAL INC 1). Only totals are written to `data/paypal_totals.json` (the record count and sum, the size bands and the property-type split), never anything about a single owner. The file ships with the repository.

```bash
uv run python scripts/paypal_totals.py --download   # about 3.3 GB of downloads; only needed to check the number
```

Homing does not pay any of these state-held records. They show how much money ends up with the state when owners are not reached in time.

## How PayPal is used

- **PayPal's payout records are the source.** `scripts/seed_uncollected.py` gave Larkfield real uncollected payouts in the sandbox: from the business's sandbox account it sent three small seller payouts ($18.50, $27.30 and $42.00) to three made-up seller addresses at example.com, recorded the batch ids in `data/payout_history.json` with the sellers' invented names, and waited until PayPal reported every one `UNCLAIMED` (items `P9CCZKAJZWZKY`, `8Q798EY279L28`, `P9XWZE6S3HJSE`). **Import uncollected payouts from PayPal** on the business's desk (`POST /api/import/paypal`) asks PayPal for each of those batches live (`GET /v1/payments/payouts/{batch_id}`) and adds every item PayPal reports `UNCLAIMED` or `RETURNED` to the dormant balances, tagged "From PayPal" with the PayPal item id and its state in plain words ("Uncollected in PayPal since 12 November; PayPal returns it on 12 December"). Importing again adds nothing twice. Such a balance can be searched, claimed and checked, but paying it is refused ("Held by PayPal...") until PayPal returns the first payout; Homing asks PayPal for the item's status before deciding.
- **Payouts** pays the owner: `POST /v1/payments/payouts` with a `sender_batch_id` made from the claim and attempt number, so a retry after a crash cannot pay twice. Status comes from `GET /v1/payments/payouts-item/{id}`. A claim shows Paid only when PayPal reports the item `SUCCESS`. On a paid claim, **See PayPal's record** (`GET /api/claims/{id}/paypal-record`) fetches that item from PayPal again and shows PayPal's own fields as returned: `transaction_status`, `payout_item_id`, `payout_batch_id`, `transaction_id`, `time_processed`, `amount` and `payout_item_fee`. When a payment is not collected, Homing will not send it to another contact until PayPal releases the first one (`POST /v1/payments/payouts-item/{id}/cancel`).
- **Invoicing** bills the business, never the owner: once an owner is paid, `POST /v2/invoicing/invoices` and `.../send` create one fee invoice per claim with a fixed invoice number, found again with `POST /v2/invoicing/search-invoices` after a crash. Each invoice row on the business's desk links to PayPal's own invoice page ("Open in PayPal").
- **Two accounts.** The business's own sandbox app (`~/.paypal_sandbox`) pays the owners. An optional second sandbox app (`~/.paypal_sandbox_homing`, or `PAYPAL_INVOICER_CLIENT_ID` and `PAYPAL_INVOICER_SECRET`) is Homing's own PayPal account: it issues the fee invoice to the business at `HOMING_HOLDER_EMAIL`, and the timeline says the invoice came from Homing's account. With only one app, the payouts and the invoice both come from that one account.
- **Webhooks**: `POST /api/webhooks/paypal` checks each delivery with `POST /v1/notifications/verify-webhook-signature`, ignores repeats and out-of-order events, applies payout and invoice events, and writes "confirmed by PayPal webhook" in the audit trail. Each claim's timeline says whether a webhook or polling confirmed the payment. With a webhook set up, the server waits 60 seconds after sending a payout (`HOMING_WEBHOOK_GRACE`) before it also asks PayPal itself, so a delivery that never arrives still ends in the right state. Without a webhook the server asks PayPal for the status (polling).

What was exercised live in the PayPal sandbox, on 12 November 2026, as counted by `uv run python scripts/live_tally.py` from the local settlement database (`data/settlement.sqlite`); the output is kept in `data/live_tally.txt`; the database itself is not in the repository. 32 payouts were sent, and 2 more were refused by PayPal before sending with `INSUFFICIENT_FUNDS`, which Homing reports plainly. 20 were collected and reported `SUCCESS`: 18 by the sandbox personal account `sb-43y9ma53877422@personal.example.com` and 2 by `sb-vgzm153858978@personal.example.com`. 12 went to made-up addresses with no PayPal account and are reported `UNCLAIMED`. PayPal's sandbox never accepted a cancel of an unclaimed payout: 6 requests came while the batch was still processing (`BATCH_NOT_COMPLETED`) and 6 got HTTP 500, so we could not confirm that a cancel works there. No payout was sent again to a new contact: Homing kept the money held and sent nothing twice. 20 fee invoices were created and sent (the tally counts 21 sends because for one claim the webhook and Homing's own status check confirmed the payout within two seconds of each other, and both recorded sending the same invoice); none has been paid. 30 webhook deliveries were verified with PayPal and recorded: `PAYMENT.PAYOUTS-ITEM.SUCCEEDED` 5 (3 changed a claim, 1 was noted with no change because Homing had already confirmed that payout by asking PayPal, 1 was not one of this server's claims), `PAYMENT.PAYOUTS-ITEM.UNCLAIMED` 5 (2 changed a claim, 3 not one of this server's claims), `PAYMENT.PAYOUTSBATCH.PROCESSING` 10 (6 noted with no change, 4 not one of this server's claims) and `PAYMENT.PAYOUTSBATCH.SUCCESS` 10 (6 noted with no change, 4 not one of this server's claims). Of the 12 that were not this server's, 9 arrived together at the time the 3 seeded seller payouts were sent (outside Homing's claims; all are `UNCLAIMED`), and 3 arrived just after the hosted demo's payout below, which uses the same PayPal app. Payouts sent before the webhook was registered were confirmed by asking PayPal. Webhooks were also registered, listed and deleted live, and a live test (`tests/test_live_paypal.py`) confirms PayPal's signature check rejects a forged delivery.

Real webhook deliveries, 12 November 2026: the server ran behind a public `cloudflared` tunnel with a webhook registered on the business's sandbox app, and one scripted run sent three payouts ($86.40 to a made-up address, then $86.40 and $212.75 to the two sandbox personal accounts). In that first run PayPal delivered 9 events, and every one passed `verify-webhook-signature`: 2 `PAYMENT.PAYOUTS-ITEM.SUCCEEDED` and 1 `PAYMENT.PAYOUTS-ITEM.UNCLAIMED` (applied), plus 3 `PAYMENT.PAYOUTSBATCH.PROCESSING` and 3 `PAYMENT.PAYOUTSBATCH.SUCCESS` (noted, no change). Each item event reached the server 34 to 35 seconds after the payout was sent, inside the 60-second wait, so all three final states (two Collected, one Not collected yet) were confirmed by the webhook and the timeline says "Confirmed by PayPal webhook". The attempt to send the uncollected payment to a second address was refused (PayPal's sandbox answered the cancel with HTTP 500), so the money stayed held and nothing was sent twice. Both fee invoices ($8.64 and $21.28) were created and sent from Homing's own sandbox app to the business's sandbox account. A second run, recording the demo video, sent $86.40 (collected) and $640.00 (to a made-up address, uncollected); both were confirmed by webhook the same way. In the same session the desk agent paid Ana's approved claim, $212.75 through PayPal Payouts (batch `MM3J2CDX5KWEW`, item `JKT3GN8B2ZJ7N`). Its wait tool gives a webhook 25 seconds and then asks PayPal, so this payout was confirmed by asking PayPal; its `PAYMENT.PAYOUTS-ITEM.SUCCEEDED` webhook arrived after and was noted with no change (the 1 noted in the tally above). This is also the claim whose fee invoice send was recorded twice. No invoice event arrived: the business never paid them, and the webhook is registered on the business's app, not on Homing's. The polling fallback after the 60 seconds did not run live, because every webhook came in time; it is tested against a fake PayPal, as are repeated, out-of-order and earlier-attempt deliveries in `tests/`.

To use real webhooks, run the server on a public https URL, then:

```bash
uv run python scripts/register_webhook.py https://your-host.example.org   # prints the webhook id
export PAYPAL_WEBHOOK_ID=<that id>                                          # and restart the server
```

The fee in this demo is an assumption, not a market rate: 10% of the balance, at most $25, paid by the business. Why a business would pay it: it owes this money to its owner or, after the dormancy period, to the state, and it is expected to try to reach owners first and keep a record. Homing drafts each notice, logs every step, and ends with the owner paid, which also gives a seller a reason to come back.

Tomasz's balance in the demo is a sample-ledger entry from 2019: an old payout that came back to the business's books. Payouts still inside PayPal's 30-day window come in through the import, and Homing will not pay them again until PayPal returns them.

## How AI is used

A language model (Claude, model "sonnet") does three kinds of work. It reads documents and writes text, and on the business's desk it runs as **the desk agent**, a tool-using agent that works the open queue and can move money through PayPal. In every case the model can stop a payment or send a case to a person, but only code, or a person who must write down why, can approve a payment, and the model never can.

- It **reads** each uploaded photo or PDF and reports what is printed on it (names, dates, addresses, emails, the issuer), its concerns, and whether it sees signs of editing. It never sees the account record. The interface shows what was read on each document. Its concerns show in red under "What looked wrong" only when it saw signs of editing; its other remarks are grey "Notes", shown only on the business's desk. In demo mode, remarks that only say a document is a sample are dropped, because the banner says it once.
- After code has set the result of a claim that is approved or needs a person, it **writes a short explanation** of the result and the next step in the claimant's language, with an English copy. A rejected claim gets no explanation and no model call.
- It **drafts the business's notice** to an owner, in the owner's likely language (from their country), with an English copy. The notice links to Homing's own address (`HOMING_PUBLIC_URL`, or the address the page was opened at). Nothing is sent.
- As **the desk agent** it chooses which tool to call next, one step at a time (next section).

Plain code compares each field with the record (name, date of birth, old address, email), rejects a document the model reports as edited, looks for impostor patterns (the same document or the same receiving account on another claim), and sets the result: approved, a person must review, or rejected. When a person approves a claim where any check is not a match, they must write why, and the reason is kept in the audit trail.

## The desk agent

The **Desk agent** tab on the business's desk has a button, **Work the queue**. The agent (`src/homing/agent.py`) works Larkfield Market's open work in this order: the payouts imported from PayPal, balances that share an owner's name, turned-down claims, and approved claims ready to settle. Each turn the model answers with one JSON object, either `{"say": "...", "tool": "...", "args": {...}}` or `{"done": true, "summary": "..."}`. Code runs the tool, adds the result to the next prompt and asks again, for at most 8 tool calls per task. A reply that is not valid JSON is asked for once more; a second bad reply stops the task.

Tools:

| Tool | What it does |
|---|---|
| `paypal_payout_item(item_id)` | asks PayPal live: `GET /v1/payments/payouts-item/{id}` |
| `paypal_batch(batch_id)` | asks PayPal live: `GET /v1/payments/payouts/{id}` |
| `ledger_lookup(query)` | the business's records for a name or email: other balances, owners with the same name, their claims |
| `claim_status(balance_id or claim_id)` | the claim's status, checks, verdict, approval and reason, and whether code would allow payment |
| `draft_notice(balance_id)` | drafts the owner notice in the owner's language (the same function as the Balances tab; nothing is sent) |
| `hold(balance_id, until, reason)` | records a hold; refused if it would end before PayPal returns a payout it still holds |
| `send_to_person(claim_id or balance_id, reason)` | puts the case on the list for a person at the business |
| `pay_claim(claim_id)` | pays through the existing settlement path (PayPal Payouts), gated by code |
| `wait_for_paypal(claim_id)` | waits for PayPal's confirmation (a verified webhook first, then asking PayPal) and reports the fee invoice |

**`pay_claim` is gated by code.** It is refused unless the claim is not turned down, nothing has been sent for it before, its verdict is approved by the checks or a person approved it with a written reason, and the owner has left a PayPal address on their claim page ("If Larkfield approves, send it to: ..."). After the gate, the same rules as the pay button still apply (the impostor rule on the receiving account, a balance PayPal still holds, never paying a balance twice). A refusal goes back to the model as the tool's result ("Refused by code: ...") and shows in red on screen. The fee invoice is not a tool: it is made by the existing settlement path once PayPal reports the payout collected.

**Why the agent's PayPal tools are our own.** As far as we could find in PayPal's Agent Toolkit and MCP server documentation, there are no Payouts tools, and the pay tool has to sit behind Homing's own approval check. So the agent gets a few narrow tools (payout item and batch lookups, hold, send to a person, pay one approved claim) that call PayPal's REST API directly. The toolkit's Invoicing tools would be a natural next step.

**Every tool call is logged**: what the model said, the tool, its arguments, the result, allowed or refused, and timestamps. `GET /api/agent/runs/{id}` returns the whole trace and each step is also written to the audit trail. **Ask it to pay a turned-down claim** gives the agent that instruction on purpose, so the refusal can be seen.

With no model and no keys the tab still works: **Show the saved run** shows `data/saved_agent_traces.json`, labelled "Saved run", written by `uv run python scripts/save_agent_traces.py` (real model and live PayPal lookups, with the pay tool stubbed in that script only). Runs made on the spot are labelled "Live run".

## What is real and what is sample

- **Real**: the California totals above (public file, computed by the script); the PayPal calls (real sandbox calls, no real money), including the three seeded uncollected seller payouts; the model's readings and writing; California's claim rules behind the claim packet, as read on 11 November 2026 from the sources listed in `src/homing/california.py`.
- **Sample**: Larkfield Market, its 41 dormant balances (`data/sample_ledger.json`, made by `scripts/make_ledger.py`), the sellers in its payout history, the people in it and their documents (`data/samples/`, drawn by `scripts/make_samples.py`, each stamped "DEMO DOCUMENT"). No real person's record is shown anywhere.

`web/src/mock.ts` is a fixture for building the screens offline (`npm run dev:mock`); screenshots and videos use the real backend.

Sample documents are stamped as samples, so outside demo mode a sample-stamped document can never be a plain match and the claim goes to a person. `HOMING_DEMO=1` (set by `scripts/demo.sh`) accepts them, and the interface shows one banner saying so.

## Quick start

You need [uv](https://docs.astral.sh/uv/) and Node.js 20 or newer, PayPal sandbox keys (next section) to pay, and a model (above) to read new documents.

```bash
git clone https://github.com/Jashann/homing.git homing && cd homing
make demo            # or ./scripts/demo.sh: installs, builds the web app, starts the server with HOMING_DEMO=1
```

Open http://127.0.0.1:8787. To drive every screen in headless Chromium and save 1280x720 screenshots: `node scripts/live_journeys.mjs --no-pay` (everything except payouts), or `--pay` for the full run with sandbox payouts (it spends $939.15 of the sandbox balance, $640.00 of it held by PayPal for 30 days). `node scripts/record_demo.mjs --scenes scripts/demo_scenes.json --no-pay` records the demo video at 1920x1080 without any payout; without `--no-pay` it sends two ($86.40 collected, $640.00 left uncollected). **Start again** in the business's view clears every claim and puts the sample balances back. By hand: `uv sync`, `cd web && npm ci && npm run build && cd ..`, `HOMING_DEMO=1 uv run python -m homing.server`.

| Setting | What it does |
|---|---|
| `HOMING_DEMO=1` | Accepts the bundled sample-stamped documents (demo banner shown) |
| `ANTHROPIC_API_KEY` | Uses the Anthropic API for the model (otherwise the `claude` program) |
| `HOMING_MODEL=off` | Turns the model off; only the saved readings are used |
| `PAYPAL_CLIENT_ID`, `PAYPAL_SECRET` | The business's sandbox keys, which pay the owners (or the file `~/.paypal_sandbox`) |
| `PAYPAL_INVOICER_CLIENT_ID`, `PAYPAL_INVOICER_SECRET` | Optional: Homing's own sandbox keys, which issue the fee invoice (or the file `~/.paypal_sandbox_homing`, or a file named by `HOMING_INVOICER_CREDENTIALS`) |
| `PAYPAL_WEBHOOK_ID` | Turns on webhook handling (see above) |
| `HOMING_WEBHOOK_GRACE` | With webhooks on, seconds after a payout is sent before the server also asks PayPal (default 60) |
| `HOMING_TEST_RECEIVERS` | Sandbox accounts several demo owners may be paid to (comma separated) |
| `HOMING_HOLDER_EMAIL` | Where the fee invoice is sent: the business's PayPal address (with two sandbox apps, the business's sandbox business account) |
| `HOMING_PUBLIC_URL` | The address the owner notices link to (default: the address the page was opened at) |
| `HOMING_PUBLIC=1` | Public demo mode for a copy on the open internet (see Hosted demo) |
| `HOMING_PUBLIC_DAILY_CAP` | In public demo mode, the most that may be paid out per UTC day, in USD (default 300) |

## PayPal sandbox keys and test accounts

1. Sign in at https://developer.paypal.com (a free account works).
2. Apps & Credentials, **Sandbox**, Create App (type Merchant), with **Payouts** and **Invoicing** ticked.
3. Put the Client ID and Secret in `~/.paypal_sandbox` as `CLIENT_ID=...` and `SECRET=...`, then `chmod 600 ~/.paypal_sandbox`. The keys are never logged or printed.
4. Optional, for invoices from a separate account: create a second sandbox app under another sandbox business account (Homing's), with **Invoicing** ticked, and put its keys in `~/.paypal_sandbox_homing` the same way. Set `HOMING_HOLDER_EMAIL` to the first account's sandbox business email so the invoice goes to the business. The first app then needs only **Payouts**.

The demo pays two sandbox personal accounts:

| Receiver | Used for |
|---|---|
| `sb-43y9ma53877422@personal.example.com` | Tomasz (the clean case), and also runs of Ana's case; collected 18 payouts live: 12 of $86.40 (Tomasz) and 6 of $212.75 (Ana), because both demo owners may be paid to the accounts in `HOMING_TEST_RECEIVERS` |
| `sb-vgzm153858978@personal.example.com` | Ana (the partial case); collected 2 payouts live |

A made-up address such as `nobody-here@example.com` has no PayPal account, so PayPal holds the payment as not collected. Sandbox logins are created free at developer.paypal.com (Sandbox, Accounts): make your own personal test account, pay it from the demo, and log in at https://www.sandbox.paypal.com to see the money arrive.

The rule "one receiving account never collects two owners' money" is relaxed only for the accounts in `HOMING_TEST_RECEIVERS` (the two above by default).

## The four sample cases

| Case | Search with | Balance | Documents (`data/samples/`) | Result |
|---|---|---|---|---|
| Clean | Tomasz Wiśniewski, t.wisniewski@example.com, Kraków, Poland | $86.40 | `clean/`: identity card, old statement | Approved; explanation in Polish |
| Partial | Ana Brennan, former name Ana Kowalski, Chicago, United States | $212.75 | `partial/`: identity card in her married name, a bill at her Dublin address, a marriage certificate (Kowalski to Brennan) | The name matches through the certificate, but the address does not: a person must review and write why |
| Impostor | Wei Zhang, Vancouver; choose the balance "between $1,000 and $2,500" | $1,240.00 | `impostor/`: a different Wei Zhang's card (other birth date and address) | Rejected |
| Edited | Mohammed Al-Rashid, Dubai | $1,875.30 | `edited/`: an identity card with the name pasted in | The model reports signs of editing; code rejects it |

`data/samples/uncollected/` also holds J. R. Okafor's documents (B006, $640.00, a clean match), used to show a payout to an address nobody collects from.

Searching **Wei Zhang** with only **Toronto** shows two balances as "possible", each with the reason. Four balances were already handed to California; for those the owner can download a claim packet to review, sign and send.

## Try it without keys

Run it with `make demo` and no keys at all: the four sample cases above work from the saved readings in `data/saved_readings.json`, so you can search, check documents, see what the model read, approve Ana's claim on the business's desk, and see the edited card turned down. Paying needs free PayPal sandbox keys (next section), and so does importing the seeded payouts from PayPal. To see the money: log in at https://www.sandbox.paypal.com as the receiving sandbox personal account and open its activity, or press **See PayPal's record** on a paid claim, which fetches PayPal's own record of that payout.

## Hosted demo

Hosted demo: https://homing-955819688070.us-central1.run.app

The hosted copy has no language model attached, so it uses the saved readings for the sample documents and shows the desk agent's saved run. Paying is live: on 12 November 2026 it sent one $86.40 test payout through PayPal Payouts (item `CPLFJDRA883NU`, reported `SUCCESS`, processed at 14:47:18 UTC), which is not in the local payout count above; its 3 webhook deliveries reached the local server and are among the 12 that were not this server's claims.

The hosted copy runs with `HOMING_PUBLIC=1`, so a visitor can send at most $300 of sandbox test money per day (the total resets if the hosted instance restarts). Every payout (Send it home, Use a different address, and the desk agent's `pay_claim`) passes one guard in code just before money would leave: the receiver must be a PayPal sandbox or made-up address (ending in `@personal.example.com` or `@example.com`), and the payouts already sent since midnight UTC, read from the settlement database, plus this one must stay within `HOMING_PUBLIC_DAILY_CAP` (300 USD unless set). A refused payout sends nothing and says why in one plain sentence. **Start again** works at most once a minute, uploads are limited to 5 MB per file and 5 files per claim, and when no model is connected **Work the queue** shows the saved run from a real session instead of failing. The demo banner then adds one line: payments go only to PayPal sandbox test accounts.

## Limits

- The ledger is sample data; a real business would load its own dormant-balance report.
- The claim packet covers California only.
- Documents are compared, not authenticated: a good forgery of a matching document could pass the checks, which is why doubtful cases go to a person.
- Webhooks need a public https URL (a tunnel such as `cloudflared tunnel --url http://127.0.0.1:8787` works for a demo, but its address changes each time, so the webhook must be registered again). Without one the app polls PayPal every few seconds.
- In this demo anyone can open the business's view. In a real deployment it would sit behind the business's own sign-in.
- **Start again** clears Homing's claims but not payments already made in the sandbox.
- Every payout spends the sandbox business account's balance, and an uncollected one stays held until PayPal returns it after 30 days. When the balance runs out PayPal answers `INSUFFICIENT_FUNDS`; use a sandbox business account with a larger balance.

## Tests

```bash
uv run pytest -q            # 145 tests: API, the desk agent and its pay gate, payout state machine, checks, privacy of rejected claims and search, PayPal history import, saved readings; PayPal and the model faked
uv run pytest -m live -q    # the money layer against the real PayPal sandbox (needs keys; makes small payouts)
```

## License

MIT. See [LICENSE](LICENSE).
