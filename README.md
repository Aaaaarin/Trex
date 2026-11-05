# Trex

Trex runs a distributor's nightly truck dispatch desk: decide what can go, offer extra stock to fill a light truck, write orders and load sheets, then track driver confirmations and misses.

It replaces part of the dispatch and order coordinator's work: reconciling records, allocating limited stock, calling retailers, chasing replies and recording what happened. The customer is a distribution operation. Who pays: **likely the brand, untested**. Neither willingness to pay nor retailer response rates have been validated.

## What works today

The prototype takes a dispatch lead's free-text request and uses tools to look up masked records. It can ask for clarification when a product is ambiguous, inspect invoice gaps, build a truck board and compare go, sell-to-fill, hold and, where feasible in the cost model, merge options.

The allocation solver sets quantities within its supplied stock and weight limits. It gives earlier units more value and uses retailer-type priorities. Batch assignment then applies remaining-life rules. Offers use purchase history, stock estimates and ageing-stock signals; replies can add demand to the plan. The agent can write orders to a fault-injected test order system, issue load sheets, notify stops, record driver-reported deliveries or failures, and maintain a miss ledger.

The web app exposes the truck state, decisions and evidence. A configured team phone can send replies, approvals, voice notes and a delivery photo through the messaging adapter. Other participants are simulated. This is a working prototype of coordination, not a deployed distributor integration.

## What is real, estimated or simulated

| Input or result | Status |
|---|---|
| Invoice history | Masked invoice lines in `data/masked/invoice_lines.csv`. Invoices establish billing, not shelf stock or delivery. |
| Orders | Next-day bills stand in for orders. Actual requested quantities are unavailable. |
| Capacity and fill target | Stand-ins. Configured capacities use historical load statistics, not measured vehicle specifications. The target still needs operator confirmation at this distribution level. |
| Cost, margin and lost-sale probability | Stand-ins. Option prices are scenario comparisons, not measured savings or recovered revenue. |
| Stock and shelf life | Stand-ins. Stock is constructed from later bills; shelf-life rules and interpretation of batch dates are assumptions. |
| Retailer types | Stand-ins inferred from records, not confirmed classifications. Retailer stock ranges are estimates. |
| Upstream branch, plant, purchase-order and vehicle data | All stand-ins. Trace and vehicle matching are demonstrations, not live upstream feeds. |
| Order system | Local test implementation with injected failures, not a production integration. |
| Delivery proof | Simulated apart from one live team phone in the demo. A phone message or photo demonstrates the workflow, not a real commercial delivery or independent verification of goods. |

The invoice view has an as-of filter, but this is **not a leakage-free forecasting evaluation**: next-day invoices supply orders, later invoices supply stock stand-ins and simulated replies, and capacities use monthly statistics. No result should be presented as proven business impact.

## Where a person is still needed

The agent is instructed to ask when ambiguity changes the decision, a truck must go below target, stock would be taken from another promise, or a credit override, substitution or excessive discount is needed. Tools explicitly check some of these boundaries, including scarce-stock requests and below-target dispatch. The full policy is not yet enforced independently of the model.

A person must also validate operational inputs and unresolved causes. An invoice gap cannot establish a stockout. Missing or conflicting records must remain visible. A driver message is required to record a drop; dispatch alone is not proof. The CLI automatically approves exceptions for demonstration, so its completion does not establish autonomous handling of those decisions.

Other limits: merge is a priced option, not a verified cross-truck execution workflow. Batch assignment can return quantities with no suitable batch. The numeric evidence check is not a semantic fact checker, and some generated load-sheet notifications bypass it. Message deduplication uses saved keys; it does not establish guaranteed external delivery after every possible crash.

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

For an interactive agent demo, configure `CHATGPT_API_KEY` in your local environment. `TREX_MODEL` selects the model and `TREX_EFFORT` selects reasoning effort. Runtime configuration also loads a private local dotenv file; never commit credentials. Agent execution uses a paid model API.

```powershell
.\.venv\Scripts\python.exe -m uvicorn trex.server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Submit a request such as `V2 wali gaadi kal jayegi? Load check karo.` Review evidence and answer exceptions in the app. The configured demonstration uses the evening of 2026-10-15 and plans 2026-10-16. Starting the server resumes unfinished runs, which can also trigger model calls.

Optional live phone: configure `TELEGRAM_BOT_TOKEN` and use only the team's own bot/chat. `TELEGRAM_CHAT_ID` can specify the chat; the poller can also register a chat from an incoming message. Keep the bot private to the team. The current adapter is a demo, not a production identity or access-control system. With no live phone configured, replies and delivery-day events remain simulated. Do not contact real retailers or other outside parties during the event.

The terminal demonstration is also available:

```powershell
.\.venv\Scripts\python.exe -m trex.cli "V2 wali gaadi kal jayegi? Load check karo."
```

**The CLI calls the model, auto-approves open decisions and triggers the simulated delivery day.** Use the web app to demonstrate actual human decisions. Runtime state defaults to `runtime/trex.sqlite`; `TREX_DB` selects a different database.

Existing core and safety tests live in `tests/test_core.py` and `tests/test_safety.py`. They cover allocation, evidence and failure handling using local fixtures and a fake model client. The request validator deliberately avoids importing application configuration or loading credentials.

## How it is built

[Architecture diagram](docs/diagram.md)

- `trex/agent.py`: Responses API tool loop. Saves model output before executing tools and returns tool failures to the agent.
- `trex/orchestrator.py`: background workers, reply wakeups and restart handling.
- `trex/tools.py`: record lookup, planning, messaging, decisions, order writes and delivery tracking.
- `trex/ledger.py`, `normalize.py`, `belief.py`: masked invoice access, unit normalization and uncertain stock-cover estimates.
- `trex/planning.py`, `solver.py`: option pricing, offer selection, CP-SAT allocation with a greedy fallback, and subsequent batch assignment.
- `trex/gate.py`, `store.py`: numeric claims matched to evidence, SQLite records, saved turns and event history.
- `trex/channels.py`: simulated replies and a team-phone adapter. `trex/orders_api.py`: local orders with read-back after injected write errors.
- `trex/upstream.py`: trace and vehicle matching on stand-ins. `trex/server.py` and `trex/web/`: API and browser interface.

## Approaches tried and what failed

These findings come from [NOTES.md](NOTES.md), not a fresh benchmark run for this README.

| Approach | Result |
|---|---|
| Invoice alerts dashboard | Rejected: leaves all the work with people. |
| Suggested retailer orders | Rejected: crowded market and needs retailer stock nobody has. |
| Late-order recovery desk | Replaced after operator feedback: treats a lost sale as recoverable and ignores truck economics. |
| Model allocates stock | Rejected: arithmetic can be wrong and hard to audit. The solver sets allocated quantities. |
| Upstream-only load builder | No real data at that level, and a tool already exists. Kept only as a trace using labelled stand-ins. |
| Nightly dispatch with sell-to-fill | Chosen, but retailer replies remain unproven. |

Failures recorded during the build:

- Stock ranges are not better than a simple habit. On 144 frequently bought pairs, predicting a rebuy within seven days scored **46.5%, the same as assuming everyone buys weekly**, because the ranges flagged every pair as low. They do not rank well yet.
- Sell-to-fill has a low ceiling in the replay. **13 of 80 truck days reached 80% as billed. At most 40 could with offers, assuming every retailer says yes.** Forty is an upper limit, not achieved uplift.
- One shop was offered **648 kg**, enough for one offer to swallow a truck. Offer sizing was subsequently capped per shop.
- The first version messaged **18 shops to gain 299 kg**. Offers now use whole cases and start with larger buyers.
- The model rejected tool calls with reasoning on Chat Completions. The loop moved to the Responses API.

The notes also report a roughly 1.3-second full-night solve; a server crash test that resumed after eight messages and finished with 177 distinct orders without duplicate sends; read-back after a write error without duplicate orders; an unsupported 25 kg claim blocked by the evidence check; and clarification instead of guessing between two butter packs. These are recorded build observations, not general guarantees.

The recorded hardest case combines scarce butter, an urgent retailer request, a light truck, thin history and an order-system failure during writing. The scarce-stock request needs a person; the notes report that other products filled the truck and no order was duplicated. Its economics and stock remain stand-ins.

## What comes next

1. Obtain actual orders, stock by batch, trip capacities/costs, retailer types and confirmed shelf-life rules. Validate the dispatch target with the operator.
2. Pilot one distributor workflow with measured retailer replies and delivery records. Compare it against the current coordinator's decisions and a simple allocation baseline.
3. Improve stock estimates against the failed weekly-buying baseline. Measure unwanted offers and expiry exposure as well as truck fill.
4. Add a real order-system connector, scoped approvals, reliable external-message delivery and independent batch/dispatch validation. Make exceptions block execution where necessary.
5. Test the same decisions on reworded requests and failures. Measure completion without intervention, duplicate writes, unsupported claims, human time and cost per run before expanding scope.

The larger product is a dispatch service that completes work across existing systems. Production planning, full network optimization and proven commercial savings are not delivered by this prototype.
