# API contract (backend: FastAPI in src/homing/server.py, served at http://127.0.0.1:8787; frontend: web/, Vite proxy /api)

All money amounts are strings with two decimals plus a currency code. All ids are strings. Dates in the API are ISO;
the interface formats them ("14 March 1979"). Errors: HTTP 4xx/5xx with {"error": "plain sentence"}.

Two audiences read claims. The claimant's endpoints (GET /api/claims/{id}, documents, pay, refresh, new-contact) show
a rejected claim as one fixed sentence and the attempts left, with no amount. The business's desk (GET /api/claims, approve) sees
every check, flag and remark.

## The number

GET /api/totals
 -> {"as_of":"2026-11-04","source":"California State Controller, unclaimed property records, file dated 2026-11-04",
     "source_url":"https://www.sco.ca.gov/upd_download_property_records.html",
     "holder_records":2054035,"holder_total":"125083033.46",
     "bands":[{"label":"Under $10","records":1070847,"total":"2884678.92"},
              {"label":"$10 to $99","records":752671,"total":"24892257.26"},
              {"label":"$100 to $999","records":214589,"total":"52786324.68"},
              {"label":"$1,000 and over","records":15928,"total":"44519772.60"}],
     "over_100":{"records":230517,"total":"97306097.28"},"over_1000":{"records":15928,"total":"44519772.60"},
     "what_is_counted":"records that PayPal reported to California as unclaimed property",
     "property_types":[{"label":"Miscellaneous intangible property","records":1918703,"total":"115698228.41"}, ...12 types]}
 Totals only (from data/paypal_totals.json): nothing about any single owner.

## The business and its balances

GET /api/meta
 -> {"holder":{"name":"Larkfield Market","kind":"an online marketplace that pays its sellers through PayPal","sample":true},
     "demo":true,"test_receivers":["sb-43y9ma53877422@personal.example.com","sb-vgzm153858978@personal.example.com"],
     "model_available":true,"public":false}   # public: HOMING_PUBLIC=1 (README, Hosted demo)

GET /api/ledger?status=
 -> {"balances":[Balance]}
Balance = {"id","owner_display","last_email_masked","last_city","last_country","amount","currency","last_activity",
           "status","sent_to_state_on","source","paypal"}
  status: DORMANT | MATCHED | PROVEN | PAYING | PAID | UNCLAIMED | RETURNED | ESCHEATED
  sent_to_state_on: ISO date (ESCHEATED only, else null)
  source: "Ledger" (the business's report) | "PayPal Payouts" (imported from its PayPal payout history)
  paypal: null | {"item_id","batch_id","status":"UNCLAIMED|RETURNED","since":"2026-11-12","returns_on":"2026-12-12",
                  "words":"Uncollected in PayPal since 12 November; PayPal returns it on 12 December"
                          | "Returned by PayPal, ready to pay again"}

POST /api/import/paypal
 For every batch id in data/payout_history.json, asks PayPal live (GET /v1/payments/payouts/{batch_id}) and adds each
 item PayPal reports UNCLAIMED or RETURNED to the dormant balances (id "PP-<payout_item_id>"). Running it again adds
 nothing twice; an item whose PayPal status changed is updated.
 -> {"batches_checked":3,"items_checked":3,"added":3,"updated":0,"balances":[Balance]}
 404 when there is no payout history; 502 when PayPal cannot be reached.

POST /api/balances/{id}/notice
 -> {"language":"pl","subject":"..","body":"..","english_subject":"..","english":"..","source":"LIVE|SAVED","link":"http://127.0.0.1:8787"}
 The model drafts the business's notice to the owner in the owner's likely language. The link to Homing is
 HOMING_PUBLIC_URL, or the address the request came to. Nothing is sent.

POST /api/demo/reset -> {"ok":true,"balances":41}   # reloads the sample ledger and clears every claim and import
                                                    # (public demo: once a minute, 429 otherwise)

## Search

POST /api/search  {"name","former_names":[..],"emails":[..],"places":[{"city","country"}]}
 -> {"matches":[{"balance_id","owner_display","amount_band":"between $50 and $100","currency","city":"Kraków"|null,
                 "strength":99,"band":"VERY_LIKELY|POSSIBLE|UNLIKELY","probability":0.99,"reasons":[".."],
                 "doubts":[".."],"status","sent_to_state_on"}]}
 Before a claim is opened there is no exact amount (only amount_band) and the city is given only when the searcher
 typed it; another balance with the same name is named by its city only when that city was typed ("in another city"
 otherwise). strength (0 to 100) is a transparent heuristic, never shown as a probability; "probability" (strength / 100)
 stays for older clients.

## Claims

POST /api/claims  {"balance_id","claimant":{"name","email","country","language","former_names":[..]}}  -> Claim
GET  /api/claims/{id}                                                                                    -> Claim (claimant)
GET  /api/claims                                                                     -> {"claims":[Claim]} (business)
POST /api/claims/{id}/documents   multipart files[] (photos or PDFs)                                    -> Claim (claimant)
POST /api/claims/{id}/approve     {"approver":"name","reason":"why you are satisfied"}                 -> Claim (business)
 reason REQUIRED (400 without it) whenever any check is not MATCH; 409 for a rejected claim
POST /api/claims/{id}/pay         {"receiver":"email or +phone","receiver_type":"EMAIL|PHONE","currency":"USD"} -> Claim
 409 unless proof.verdict == APPROVED or a person approved; 409 with the "Held by PayPal" sentence while the balance's
 earlier PayPal payout is still UNCLAIMED (PayPal is asked for its status first)
POST /api/claims/{id}/refresh     -> Claim   (asks PayPal; with webhooks on, waits HOMING_WEBHOOK_GRACE seconds first)
POST /api/claims/{id}/new-contact {"receiver","receiver_type"} -> Claim
 after UNCLAIMED or RETURNED: cancels the old item first; 409 "Held by PayPal..." when PayPal refuses; never pays twice
GET  /api/claims/{id}/paypal-record
 -> {"endpoint":"GET /v1/payments/payouts-item/<id>","fetched_at":"2026-11-12T14:05:09+00:00",
     "record":{"transaction_status":"SUCCESS","payout_item_id","payout_batch_id","transaction_id","time_processed",
               "amount":{"currency":"USD","value":"86.40"},"payout_item_fee":{"currency":"USD","value":"0.25"}}}
 PayPal's own record of the claim's payout, fetched live; 409 before anything was sent.

Claim = {"id","balance_id","status","claimant":{...},"amount","currency","source","paypal_held",
  "proof":{"verdict":"APPROVED|NEEDS_REVIEW|REJECTED|NONE","score":0.0-1.0,
           "checks":[{"field":"name|date_of_birth|address|email|document","record_value_masked","document_value",
                      "result":"MATCH|PARTIAL|MISMATCH|MISSING","note"}],
           "flags":[".."],"missing":[".."],"approved_by":null,
           "documents":[{"file","kind","thumb_url","status","problem","summary","marked_sample",
                         "read":{"names":[..],"date_of_birth","addresses":["ul. Długa 12, Kraków, 31-147, PL"],
                                 "emails":[..],"issuer","dates":[{"label","value"}]},
                         "concerns":[..],"signs_of_editing":true|false,"reading_source":"LIVE|SAVED"}],
           "explanation":{"language","text","english","source":"LIVE|SAVED|PLAIN"} | null,
           "review":{"required","reason","approved_by","approved_at"},
           "attempts":n,"attempts_left":n,"demo":true|false,"message":null},
  "payout":{"state":"NONE|SENT|CLAIMED|UNCLAIMED|RETURNED|FAILED|CANCELLED","batch_id","item_id","receiver_masked","fee",
            "attempt","confirmed_by":"WEBHOOK|POLLING|null",
            "timeline":[{"at","state","title","note","paypal_id","heard_by":"WEBHOOK|POLLING|null"}]},
  "invoice":{"id","number","amount","currency","status","payer_url"} | null}
status of a Claim: OPEN | PROOF_READY | APPROVED | PAYING | PAID | NEEDS_NEW_CONTACT | REJECTED
source: "Ledger" | "PayPal Payouts"; paypal_held: the balance's PayPal state in words while PayPal still holds it, else null

What each audience sees:
- A REJECTED claim, claimant side: amount is null (the exact balance is not shown; the business's desk still sees
  it), checks, flags, missing and documents are empty, explanation is null, the match strength (`score`) is 0,
  and message is "We could not confirm this claim. Larkfield Market has been told." with attempts and attempts_left.
  The model is not asked to explain a rejection.
- Any other claim, claimant side: a document's concerns are empty unless signs_of_editing is true.
- The business's side: everything, including the model's concerns (grey notes in the interface; red "What looked wrong"
  only when signs_of_editing is true). In demo mode, concerns that only say a document is a sample are dropped.
- A claim is APPROVED only by code (every check a match). The model can make it REJECTED (signs of editing) or
  NEEDS_REVIEW; only a person can approve that, with a written reason.

Timeline titles: "Prepared", "Sent through PayPal", "Collected", "Not collected yet", "Held by PayPal",
"Fee invoice created", "Fee invoice sent to Larkfield Market", "Returned to Larkfield Market", "Not delivered",
"Cancelled", "Not accepted by PayPal", "Cancel requested", "Trying a new contact", "Fee invoice delayed",
"Fee invoice paid".

## The desk agent

POST /api/claims/{id}/payout-address {"receiver":"owner's PayPal email"} -> Claim (claimant)
 The owner leaves the PayPal address to pay if the business approves; stored on the claim as payout_address.
 409 for a rejected or already paid claim.

POST /api/agent/runs {"mode":"queue|brakes","dry_pay":false} -> Run   (409 while another run is working)
     public demo with no model: the saved Run of that mode, status "done", label "Saved run", plus "note"
GET  /api/agent/runs/{id}                                     -> Run   (poll until status is "done")
GET  /api/agent/saved -> {"label":"Saved run","saved_at","note","queue":Run,"brakes":Run}  (404 when none saved)
Run = {"id","mode","label":"Live run","rehearsal":bool,"status":"running|done","started_at","ended_at",
       "tasks":[{"kind":"paypal|same_name|rejected|approved|waiting|brakes","group":"a|b|c|d|e","title","balance_id",
                 "claim_id","status":"waiting|running|done|stopped|error","summary",
                 "steps":[{"n","say","tool","args","result","data","outcome":"allowed|refused|error",
                           "asked_at","started_at","ended_at"}]}],
       "summary":{"found","held","notices","sent_to_person","paid","pay_sent","refused","tool_calls"}}
 queue: the imported PayPal payouts (worked side by side), then same-name balances, turned-down claims and claims
 ready to settle. brakes: the agent is told to pay a turned-down claim; code refuses. dry_pay: a rehearsal, the pay
 gate runs but nothing is sent. Tools and the pay_claim gate: README, "The desk agent". A refused step's result
 starts "Refused by code: ".

Why the agent's PayPal tools are our own: as far as we could find in PayPal's Agent Toolkit and MCP server
documentation, there are no Payouts tools, and the pay tool has to sit behind Homing's own approval check. So the agent
gets a few narrow tools (payout item and batch lookups, hold, send to a person, pay one approved claim) that call
PayPal's REST API directly. The toolkit's Invoicing tools would be a natural next step.

## Money and state

POST /api/webhooks/paypal         (PayPal events; verified with PayPal; idempotent) -> {"ok":true,"outcome":".."}
GET  /api/scoreboard
 -> {"found":n,"proven":n,"paid":n,"unclaimed":n,"returned":n,"rejected":n,"paid_total":"..","fees_invoiced":"..",
     "median_minutes_to_paid":n}   # the interface shows paid_total and fees_invoiced large, and the counts
POST /api/state-claim/{balance_id} -> {"pdf_url":"/api/files/state-claims/..","steps":[".."]}
 balance already ESCHEATED: a claim packet prepared for the owner to review, sign and send

## Sample cases

data/samples/: clean (Tomasz, B001, APPROVED), partial (Ana, B002: identity card in her married name, a bill at her
Dublin address, a marriage certificate linking Kowalski to Brennan; NEEDS_REVIEW), impostor (a different Wei Zhang
claims B003; REJECTED), edited (an identity card for B007 with the name pasted in; the model reports signs of editing
and code rejects it), uncollected (J. R. Okafor, B006, APPROVED; used to show a payout nobody collects).
In the demo Tomasz is paid to test_receivers[0] and Ana to test_receivers[1].
