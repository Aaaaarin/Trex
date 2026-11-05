# Submission draft

Copy the contents of each block into the corresponding field. Links and member details are intentionally blank until verified. Do not paste this preparation note into the form.

## Team name

```text
Trex
```

## One line

```text
Trex runs a distributor's nightly dispatch desk to meet delivery commitments at an acceptable total cost: price each truck decision, allocate scarce stock, write load sheets, find a driver and follow each drop to a recorded outcome.
```

## Theme

```text
Supply chain
```

## How the product works

```text
A distributor's coordinator must decide which trucks go, what goes on them and who receives a partial order, using incomplete records and phone calls. Trex automates parts of that service. Its objective is meeting delivery commitments at an acceptable total cost, not filling trucks. The likely payer is the brand, untested.

A dispatch lead submits a messy request. The agent, running on gpt-6.1-sol through the OpenAI Responses API, resolves masked records, checks invoice history and asks when ambiguity changes the decision. Tools compare go, hold and, where feasible, sell-to-fill and merge costs. An allocation solver sets quantities under stock and vehicle-weight limits, then assigns batches using remaining-life rules. When selling is priced best, the agent sends targeted offers and replies update the plan. Going under target needs a person's approval for that exact load. A validator recomputes limits from source data and freezes a numbered plan. The agent writes orders to a test order system and issues load sheets. Code with no model turn then offers the job to drivers with stand-in pay, assigns one by default if nobody accepts, and tells the dispatcher who comes and what to hand over. A retailer line answers status from saved records. Driver-reported drops and misses are recorded.

The model selects tools and explains their outputs. Numeric claims on gated messages must cite matching evidence. Saved turns, keyed messages and order read-back support crash and retry handling. The web app shows decisions, supporting records and outstanding work. Generated load-sheet, driver, dispatcher and retailer-line messages bypass the numeric gate. Under-target, promised-stock, hold and merge limits are enforced in code. Ambiguity, credit and substitution remain agent instructions.

Masked invoice history is real. Capacity, cost, stock, shelf life, retailer types and all upstream data are stand-ins. Drivers, pay, willingness to accept, report times and gate times are also stand-ins. ETA is an estimate from stops closed, with no GPS. Next-day bills stand in for orders, and later bills help construct stock and simulated replies. Delivery proof is simulated apart from one live team phone on Telegram; that phone demonstrates the workflow, not a commercial delivery, and nothing inspects a photo. Real calls and WhatsApp are not built. The CLI auto-approves exceptions; the interactive demo shows where a person is needed. Merge has not been tried with real vehicles.

The build notes record failures plainly. Stock ranges show no predictive skill: they scored 46.5% on 144 frequently bought pairs, equal to the weekly-buying baseline, because every pair was flagged low. Only 13 of 80 truck days reached the stand-in 80% target as billed; at most 42 could with offers if everyone accepted, an upper limit. Some retailer and product pairs average 648 kg or more per bill, so offers are capped at a stand-in 90 kg per shop. Filling trucks as the goal was the wrong objective and was replaced. These results are not measured savings or recovered revenue.

The hardest case combines scarce stock, a light truck, thin history and an order-write failure. Tests show the scarce request needs a person's approval for that exact quantity and that a write failure creates no duplicate order. Stock and economics in that case are stand-ins. The demo's finished work is saved decisions, test orders, load sheets, delivery records and a miss ledger, with unresolved work still visible.
```

## What comes next

```text
Validate this workflow with one distributor and replace the stand-ins with actual orders, stock by batch, vehicle capacities, trip costs, driver and pay records, shelf-life rules and retailer types. Confirm whether retailers answer the offers and whether the brand will pay. Improve stock estimates against the weekly-buying baseline. Add a real order-system connector and reliable external-message delivery, and enforce credit and substitution limits in code. Measure commitments met, total cost, human interventions, unsupported claims, duplicate writes, unwanted offers and cost per run before expanding to more routes. Full network and production planning are later work.
```

## Public repository URL

```text

```

## Two-minute video URL

```text

```

## Public high-level diagram URL

```text

```

## Member details

Member 1 name:

Member 1 registration email:

Member 1 other required details:

Member 2 name:

Member 2 registration email:

Member 2 other required details:

Member 3 name:

Member 3 registration email:

Member 3 other required details:

Member 4 name:

Member 4 registration email:

Member 4 other required details:

## Checks before pasting

- Match member details to the accepted registration. Omit unused member slots.
- Confirm the repository is public and the video and diagram open without signing in. `docs/diagram.md` is the diagram source; its public file URL can be used once verified.
- Start the video with a request and end with the resulting records. Show evidence, one failure and the boundary between a person, a live team phone and simulation.
- Use only masked entities and team-owned communication channels. Do not imply that the fixture validator measures model accuracy or that simulated outcomes establish business savings.
