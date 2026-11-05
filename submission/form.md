# Submission draft

Copy the contents of each block into the corresponding field. Links and member details are intentionally blank until verified. Do not paste this preparation note into the form.

## Team name

```text
Trex
```

## One line

```text
Trex runs a distributor's nightly dispatch desk: allocate scarce stock, sell to fill light trucks, write load sheets and follow each drop to a recorded outcome.
```

## Theme

```text
Supply chain
```

## How the product works

```text
A distributor's coordinator must decide which trucks go, what goes on them and who receives a partial order, using incomplete records and phone calls. Trex automates parts of that service. The likely payer is the brand, untested.

A dispatch lead submits a messy request. The agent resolves masked records, checks invoice history and asks when ambiguity changes the decision. Tools compare go, sell-to-fill, hold and possible merge costs. An allocation solver sets quantities under stock and vehicle-weight limits, then assigns batches using remaining-life rules. The agent sends targeted offers; replies update the plan. It asks a person about policy exceptions, writes orders to a test order system, issues load sheets and tracks driver-reported drops and misses.

The model selects tools and explains their outputs. Numeric claims on gated messages must cite matching evidence. Saved turns, keyed messages and order read-back support crash and retry handling. The web app shows decisions, supporting records and outstanding work. Some generated notifications bypass the numeric gate, and policy enforcement is not yet complete independently of the model.

Masked invoice history is real. Capacity, cost, stock, shelf life, retailer types and all upstream data are stand-ins. Next-day bills stand in for orders, and later bills help construct stock and simulated replies. Delivery proof is simulated apart from one live team phone; that phone demonstrates the workflow, not a commercial delivery. The CLI auto-approves exceptions; the interactive demo shows where a person is needed. Merge pricing is not a verified cross-truck execution workflow.

The build notes record failures plainly: stock ranges scored 46.5% on 144 frequently bought pairs, equal to the weekly-buying baseline because every pair was flagged low. Only 13 of 80 truck days reached the 80% target as billed; at most 40 could with offers if everyone accepted. A shop was offered 648 kg before offer sizing was capped. The first offer version contacted 18 shops to gain 299 kg. Reasoning tool calls failed on Chat Completions, so the loop moved to the Responses API. These results are not measured savings or recovered revenue.

The recorded hardest case combines scarce stock, a light truck, thin history and an order-write failure. It needs a person's stock decision. The notes report completion using other products and no duplicate orders. The demo's finished work is saved decisions, test orders, load sheets, delivery records and a miss ledger, with unresolved work still visible.
```

## What comes next

```text
Validate this workflow with one distributor and replace the stand-ins with actual orders, stock by batch, vehicle capacities, trip costs, shelf-life rules and retailer types. Confirm whether retailers answer the offers and whether the brand will pay. Improve stock estimates against the weekly-buying baseline. Add a real order-system connector, scoped approvals, reliable external-message delivery and independent batch validation. Measure completed work, human interventions, unsupported claims, duplicate writes, unwanted offers and cost per run before expanding to more routes. Full network and production planning are later work.
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
