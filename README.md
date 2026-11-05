# Trex

Trex runs a distributor's nightly truck dispatch desk. Its objective is to meet delivery commitments at an acceptable total cost, not to fill trucks. For a truck it prices going, selling to fill, merging and holding, then allocates stock, writes orders and load sheets, finds a driver and tracks each drop to a recorded outcome. Going under target is allowed when a person approves that exact load.

It takes on part of the dispatch and order coordinator's work: reconciling records, allocating limited stock, messaging retailers and drivers, chasing replies and recording what happened. The customer is a distribution operation. Who pays: **likely the brand, untested**. Neither willingness to pay nor retailer response rates have been validated.

## What works today

The prototype takes a dispatch lead's free-text request and uses tools to look up masked records. The agent runs on `gpt-6.1-sol` through the OpenAI Responses API. It can ask for clarification when a product is ambiguous, inspect invoice gaps, build a truck board and compare go, hold and, where they are feasible in the cost model, sell-to-fill and merge. Selling is dropped from the options when offers cannot reach the target.

The allocation solver sets quantities within its supplied stock and weight limits. It gives earlier units more value and uses retailer-type priorities. Batch assignment then applies remaining-life rules. Pieces with no suitable batch stay off the plan and are listed as unplaced. Offers use purchase history, stock estimates and ageing-stock signals, and only stock that retailer type can take. Replies can add demand to the plan.

Each dispatch outcome has its own path. To dispatch, a validator recomputes stock, batch and capacity limits from source data and freezes a numbered, hashed plan version. Orders, the load sheet and the driver job all read that version. Hold and merge each need an approved decision naming that truck, and merge also names the host. A yes that arrives after commit becomes a proposed revision and does not change the plan. Orders go to a fault-injected test order system, not a real one.

After a load sheet is issued, code with no model turn offers the job to drivers with a stand-in pay figure. Pay rises each round, for at most three rounds, up to the lower of a cap and what going still saves over holding. If nobody accepts, the job goes to a driver by default. A no-show reopens the job at top pay without that driver. The dispatcher is told who comes and what to hand over by product and batch, and logs each truck in and out. A truck with a job starts its drops only after it is logged out.

A retailer line answers status questions from saved records with no model turn. Messages go through keyword matching first, and the model classifies only what the keywords cannot place. Orders and discount requests go to the dispatch agent, and the shop is told nothing is promised yet. A shop that says it is busy gets one callback.

A driver's message is required to close a drop, and the agent is instructed to log failed drops in a miss ledger. The web app shows trucks, decisions, evidence, drivers, retailers, batches and misses. A configured team phone can send replies, approvals, voice notes and a delivery photo through Telegram, which is the only live channel. A shop can place an order on a voice call in the browser, through OpenAI Realtime. Calls to a phone number and WhatsApp are not built, because the Twilio account has no number and no balance. Other participants are simulated. This is a working prototype of coordination, not a deployed distributor integration.

## What is real, estimated or simulated

| Input or result | Status |
|---|---|
| Invoice history | Real but masked: 23,282 lines from October 2026 across two distributors in `data/masked/invoice_lines.csv`. The demo plans for distributor A. Invoices establish billing, not shelf stock or delivery. |
| Orders | Next-day bills stand in for orders. Actual requested quantities are unavailable. |
| Capacity and fill target | Stand-ins. Each capacity is that vehicle label's 95th percentile daily load in October, not a measured specification, and at least one label covers several real trucks. The 80% target still needs operator confirmation at this distribution level. |
| Cost, margin, lost-sale chance, offer acceptance and discount limit | Stand-ins. Option prices are scenario comparisons, not measured savings or recovered revenue. |
| Stock and shelf life | Stand-ins. Stock is constructed from later bills, and one butter pack is set to 60% of plan-day orders to create the scarce case. Shelf-life rules and interpretation of batch dates are assumptions. |
| Retailer types | Stand-ins inferred from records, not confirmed classifications. Retailer stock ranges are estimates. |
| Retailer replies | Simulated from later bills, except on the team phone. |
| Drivers, pay, willingness to accept, report times and gate times | All stand-ins, invented until the operator confirms them. The invoices hold none of this. |
| Driver and depot history, ratings, earnings, on-time and acceptance figures | All stand-ins in `data/standins/history.json`, from `scripts/make_history.py`, and labelled on screen. Ratings decide who is offered a job first, so that order rests on invented numbers. |
| A shop's past bills on the phone screen | Real but masked, up to the plan evening. Only the delivery status beside each bill is a stand-in. |
| ETA | An estimate from gate-out time, stops closed and a stand-in 12 minutes per stop. There is no GPS. |
| Upstream branch, plant, purchase-order and vehicle data | All stand-ins. Trace and vehicle matching are demonstrations, not live upstream feeds. |
| Transporter list | Real companies from Crustdata, looked up on a public test lane rather than the operator's masked routes. It contacts nobody. Matching is by head office, so service on the lane is not confirmed, and refrigerated capability is what each company says of itself. In a five-lane test, 40 of 50 rows looked like real road carriers by an agent's judgement, with weaker refrigerated coverage. |
| Order system | Local test implementation with injected failures, not a production integration. |
| Delivery proof | Simulated apart from one live team phone in the demo. Trex records that a photo arrived and nothing inspects the picture. Simulated drops carry a simulated photo flag with no image behind it. A phone message or photo demonstrates the workflow, not a real commercial delivery or independent verification of goods. |

Behaviour run: [docs/eval-results.md](docs/eval-results.md) records 12 paid messy requests, one run each, at an estimated $0.82. All met the expected next action after an offline re-score. The code changed during the run, every question was approved so refusal paths were not exercised, nothing after the first load sheet was scored, and repeatability is unknown. It found five product problems, since fixed with tests but not rerun in full.

The invoice view has an as-of filter, but this is **not a leakage-free forecasting evaluation**: next-day invoices supply orders, later invoices supply stock stand-ins and simulated replies, and capacities use monthly statistics. No result should be presented as proven business impact.

## Where a person is still needed

Code enforces these limits, whatever the model says:

- Going under target needs an approval for that truck, that exact kg and that plan basis.
- Taking promised stock needs an approval for that retailer, product and quantity.
- Hold and merge need an approval naming the truck, and merge also names the host.
- Adding a stop for a shop with no truck on its route needs a person to approve the truck.
- An offer above the discount plan_offers allowed is refused.
- Stock, batch, capacity and order-saved checks block a plan or a promise.

These remain agent instructions only: asking when ambiguity changes the decision, credit overrides and substitutions. The full policy is not enforced independently of the model.

A person must also validate operational inputs and unresolved causes. An invoice gap cannot establish a stockout. Missing or conflicting records must remain visible. Dispatch alone is not proof of delivery. The CLI automatically approves exceptions for demonstration, so its completion does not establish autonomous handling of those decisions.

Other limits: merge moves stops onto a host truck in this prototype's records, and it has not been tried with real vehicles. The numeric evidence check is not a semantic fact checker. Generated load-sheet, hold, merge, driver, dispatcher and retailer-line messages bypass it. Message deduplication uses saved keys; it does not establish guaranteed external delivery after every possible crash.

## Run it

From the repository root, install the dependencies in an isolated environment. The commands below use a Windows shell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For the standalone request-fixture check, no model credentials or server are needed:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cases_valid.py -q
```

The case file contains 30 intended request outcomes, including five reworded pairs. The validator checks data references and fixture consistency only. It does not run the agent or establish its accuracy.

For an interactive agent demo, configure `CHATGPT_API_KEY` in your local environment. `TREX_MODEL` selects the model and defaults to `gpt-6.1-sol`. `TREX_EFFORT` selects reasoning effort and defaults to `medium`. Runtime configuration also loads `~/.trex.env`; never commit credentials. Agent execution uses a paid model API.

```powershell
.\.venv\Scripts\python.exe -m uvicorn trex.server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Submit a request such as `V2 wali gaadi kal jayegi? Load check karo.` Review evidence and answer exceptions in the app. The configured demonstration uses the evening of 2026-10-15 and plans 2026-10-16. Starting the server resumes unfinished runs, which can also trigger model calls.

Optional live phone: configure `TELEGRAM_BOT_TOKEN` and use only the team's own bot/chat. `TELEGRAM_CHAT_ID` can specify the chat; the poller can also register a chat from an incoming message. Keep the bot private to the team. Voice notes are transcribed with `gpt-4o-transcribe`, which is a paid call. One phone can play several people with `/driver D03 haan`, `/gate V2 in` and `/shop R-120 gaadi kahan hai`. The current adapter is a demo, not a production identity or access-control system. With no live phone configured, replies and delivery-day events remain simulated. Do not contact real retailers or other outside parties during the event.

Live phone demo: open `http://127.0.0.1:8000/demo` for all four screens, or use each on its own phone: `/call` (shop), `/driver?id=D02` (driver) and `/gate` (dispatcher). The first order starts the run and goes only to the truck carrying that shop. While a driver's screen is open, simulated drivers wait up to 120 seconds for that person to answer. While the dispatcher's screen is open, gate times wait for its taps. Other stops, drivers and shops stay simulated. The call runs on `gpt-realtime` (`TREX_VOICE_MODEL`), which is a paid API. For a phone's microphone the app needs HTTPS: set `TREX_PUBLIC_KEY` before exposing it through a tunnel, and visitors then need `?key=` on their first request.

The terminal demonstration is also available:

```powershell
.\.venv\Scripts\python.exe -m trex.cli "V2 wali gaadi kal jayegi? Load check karo."
```

**The CLI calls the model, auto-approves open decisions and triggers the simulated delivery day.** Use the web app to demonstrate actual human decisions. Runtime state defaults to `runtime/trex.sqlite`; `TREX_DB` selects a different database.

Run the full test suite with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

`tests/test_core.py`, `test_safety.py`, `test_dispatch.py` and `test_fleet.py` cover allocation, evidence, failure handling, frozen plans, approvals, drivers, the gate log and the retailer line. They use masked data, a temporary database and fake model clients, so they say nothing about the live model's accuracy. The request validator deliberately avoids importing application configuration or loading credentials. `python -m trex.replay` rewrites `data/replay_results.json`.

## How it is built

[Architecture diagram](docs/diagram.md)

- `trex/agent.py`: Responses API tool loop. Saves model output before executing tools and returns tool failures to the agent.
- `trex/orchestrator.py`: background workers, reply wakeups, the offer reply window, restart handling and the simulated delivery day.
- `trex/tools.py`: record lookup, planning, messaging, decisions, order writes and delivery tracking.
- `trex/ledger.py`, `normalize.py`, `belief.py`: masked invoice access, unit normalization and uncertain stock-cover estimates.
- `trex/planning.py`, `solver.py`, `standins.py`: option pricing, offer selection, CP-SAT allocation with a greedy fallback, batch assignment and constructed stock.
- `trex/dispatch.py`: frozen plan versions, exact-scope approvals and the validator.
- `trex/fleet.py`, `desk.py`, `fleet_api.py`: driver offers, default assignment, the dispatcher's gate log and the retailer line.
- `trex/gate.py`, `store.py`: numeric claims matched to evidence, SQLite records, saved turns and event history.
- `trex/history.py`: bill history from the invoices, and invented driver and depot history.
- `trex/phone.py`, `trex/web/call.html`, `driver.html`, `gate.html`, `demo.html`: the voice order line, the driver's and dispatcher's screens, and the page that shows them together.
- `trex/channels.py`: simulated replies and a Telegram team-phone adapter. `trex/orders_api.py`: local orders with read-back after injected write errors.
- `trex/upstream.py`: trace and vehicle matching on stand-ins. `trex/replay.py`: October replay scoring. `trex/server.py`, `state.py` and `trex/web/`: API and browser interface.

## Approaches tried and what failed

Decision history comes from [NOTES.md](NOTES.md).

| Approach | Result |
|---|---|
| Invoice alerts dashboard | Rejected: leaves all the work with people. |
| Suggested retailer orders | Rejected: crowded market and needs retailer stock nobody has. |
| Late-order recovery desk | Replaced after operator feedback: treats a lost sale as recoverable and ignores truck economics. |
| Model allocates stock | Rejected: arithmetic can be wrong and hard to audit. The solver sets allocated quantities. |
| Upstream-only load builder | No real data at that level, and a tool already exists. Kept only as a trace using labelled stand-ins. |
| Filling trucks as the goal | Replaced: a fuller truck can be worse. The objective is now meeting commitments at an acceptable total cost, and selling to fill is one priced action. |
| Nightly dispatch with priced options | Chosen, but retailer replies remain unproven. |

Failures recorded during the build. Replay figures come from `data/replay_results.json`:

- Stock ranges show no predictive skill. On 144 frequently bought pairs, predicting a rebuy within seven days scored **46.5%, the same as assuming everyone buys weekly**, because the ranges flagged every pair as low and never predicted a non-buyer.
- Sell-to-fill has a low ceiling in the replay. **13 of 80 truck days reached the stand-in 80% target as billed. At most 42 could with offers, assuming every retailer says yes.** That is an upper limit, not achieved uplift.
- Retailer and product pairs in the billing history average as much as **648 kg and 1,155 kg per bill** as of 2026-10-15, enough for one uncapped offer to take most of a small truck. Offer sizing is now capped at a stand-in 90 kg per shop.
- The first offer version messaged too many shops for too little load. Offers now use whole cases, send up to two ageing-stock offers first, then larger regular buyers, and stop at a stand-in limit of 10 questions per truck.

Tests cover the following on local fixtures: resuming a saved turn without sending twice, read-back after a write error without duplicate orders, unsupported numbers being blocked by the evidence check, and the validator refusing bad plans. `scripts/crash_test.py` and `scripts/live_checks.py` repeat crash, failed-write, under-target, merge and hold cases against the real agent. They spend model credit and save no output, so this README claims no result from them. [NOTES.md](NOTES.md) keeps a run log, labelled as such.

Three results here were reproduced from code without a model call, on stand-in inputs for the 2026-10-16 plan:

- Route 13's truck prices at Rs 956 to merge, Rs 3,015 to go separately and Rs 10,032 to hold. Previewed after batch checks, the merged load is 4,537 kg, 77.2% of the host's stand-in capacity, with 10 lines unplaced. That is under the stand-in target, so it needs an exact approval.
- With every truck at its stand-in driver settings, V1 was accepted at start pay, V2 after one pay rise and V3 went to a driver by default, on all 12 simulated run ids tried.
- The CP-SAT solve for that night's 441 lines took about 0.1 seconds on the build laptop, and a test confirms the same input gives the same plan.

The hardest case combines scarce butter, an urgent request from a retailer inferred to be a hotel with no earlier bills for that pack, a light truck and an order-system failure during writing. Tests show the scarce-stock request needs a person's approval for that exact quantity, and that a write failure creates no duplicate order. `scripts/live_checks.py` drives the combined case. Its economics and stock remain stand-ins.

## What comes next

1. Obtain actual orders, stock by batch, trip capacities/costs, retailer types, driver and pay records, and confirmed shelf-life rules. Validate the dispatch target with the operator.
2. Pilot one distributor workflow with measured retailer replies and delivery records. Compare it against the current coordinator's decisions and a simple allocation baseline.
3. Improve stock estimates against the failed weekly-buying baseline. Measure unwanted offers and expiry exposure as well as commitments met and total cost.
4. Add a real order-system connector and reliable external-message delivery. Enforce credit and substitution limits in code. Check delivery photos.
5. Test the same decisions on reworded requests and failures. Measure completion without intervention, duplicate writes, unsupported claims, human time and cost per run before expanding scope.

The larger product is a dispatch service that completes work across existing systems. Production planning, full network optimization and proven commercial savings are not delivered by this prototype.
