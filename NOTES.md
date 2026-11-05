# Build notes

Kept through the night: what we weighed, what worked, what did not.

Labels used below:

- **[run log]**: seen in one live run on the build of that moment. The output was not saved and it has not been rerun on current code.
- **[to confirm]**: a replay figure not yet checked against the rerun.
- **[stand-in]**: rests on invented or placeholder inputs, not measurements.

## Approaches considered

| Approach | Promising because | Falls short because | Decision |
|---|---|---|---|
| Alerts dashboard from invoices | Fast, easy to demo | Leaves all the work with people | Rejected |
| Suggested orders for retailers | Clear value | Crowded market, needs retailer stock nobody has | Rejected |
| Late-order recovery desk | Bounded job | Treats a lost sale as recoverable, ignores truck economics | Replaced after operator feedback |
| LLM allocates stock | Quick to build | Arithmetic can be wrong and hard to audit | Rejected. Solver sets every quantity |
| Plant-to-branch load builder only | Matches the 18 MT trucks in the operator's document, which is not in this repo | No real data at that level, and a tool already exists | Kept as upstream trace on labelled stand-ins |
| Nightly truck decision with priced options | Joins truck use, shelf life, range selling and lost sales. The objective is meeting commitments at an acceptable total cost | Retailer replies are unproven | **Chosen** |

## What worked

- Quantities come from taxable value divided by an estimated trade price (0.87 of consumer price, a rough estimate). Rate times quantity matches value on only about 60% of lines (59.5% measured across all 23,282).
- Partial-first allocation with falling value per unit. The CP-SAT solve for the 2026-10-16 night's 441 lines takes about 0.1 seconds on the build laptop, and a test confirms the same input gives the same plan.

- Crash recovery on the real server. Killed mid-run with 8 messages sent, it resumed by itself and finished with 177 distinct orders and no identical message sent twice **[run log]** (`scripts/crash_test.py`). A test covers resuming a saved turn without sending twice.
- With a 500 error after three order writes, it read each one back and wrote no duplicates **[run log]**. Tests cover read-back after each injected write error.
- The source check blocked a real mistake: the agent worked out a 25 kg gap itself, and the check refused it because no tool had returned that number **[run log]**. Tests cover blocking a number with no matching evidence.
- The agent refused to guess between two 500 g butter packs and asked instead **[run log]**.

- The dispatch validator caught a bug in our own code on its first live run: batch codes repeat across products in the real records, and we had assumed they were unique. It refused the plan, so nothing bad was sent **[run log]**. The repeat is real: 25 of 725 batch codes for the demo distributor cover more than one product, and a test covers it.
- An unreachable truck now goes straight to a priced choice. On Route 13 it stopped selling, asked one scoped question, and went out under target because holding cost more **[run log, stand-in]**.
- Merge is now credited with the empty space it saves on the host. On stand-in costs for the 2026-10-16 plan, Route 13's truck prices at Rs 956 to merge, Rs 3,015 to go separately and Rs 10,032 to hold. After batch checks the merged load previews at 4,537 kg, 77.2% of the host's stand-in capacity, with 10 lines unplaced, so it needs an exact under-target approval **[stand-in]**, reproduced from code without a model call. Merge and hold were also run live **[run log]**.

- Driver offers run as code, so accept and reject cost no model turn. In a three-truck simulation on stand-in pay and willingness, V1 was accepted at start pay, V2 after one pay rise, and V3 went to a driver by default, on all 12 run ids tried **[stand-in]**. Pay stops at a cap, or sooner if going would no longer beat holding.

## What did not work

- Stock ranges show no predictive skill. On 144 frequently bought pairs, predicting a rebuy within 7 days scored 46.5%, the same as assuming everyone buys weekly **[to confirm]**, because the ranges flagged every pair as low and never predicted a non-buyer. They are honest about uncertainty, but they do not rank at all yet.
- Sell to fill has a low ceiling in the replay. 13 of 80 truck days reached the stand-in 80% target as billed. At most 40 could with offers, assuming every retailer says yes, so 40 is an upper limit **[to confirm]**.
- One offer could swallow a truck: a single shop was offered 648 kg **[run log]**. The billing history supports the size, with retailer and product pairs averaging as much as 648 kg and 1,155 kg per bill as of 2026-10-15. Offers are now capped at a stand-in 90 kg per shop.

- The first version messaged 18 shops to gain 299 kg **[run log]**. Offers now use whole cases, send up to two ageing-stock offers first, then larger regular buyers.
- The model first rejected tool calls with reasoning on Chat Completions, so the loop moved to the Responses API **[run log]**. The agent now runs on `gpt-6.1-sol` through the Responses API.

- Selling to fill as the goal was the wrong objective. A review pointed out that a fuller truck can be worse, and that going under target must be allowed. The objective is now meeting commitments at an acceptable total cost.
- A reply that never came left a truck waiting forever. Offers now have a stand-in 90-second reply window.
- With an unreachable target, it messaged 24 shops **[run log]**. The limit of 10 existed but was not enforced. It is now, and a test covers it.
- Accepted offers were dropped later when no batch suited that retailer. Offers now use only stock that retailer can take.
- The fake-model tests of the time did not catch the reply-window, question-limit or dropped-offer problems. Live runs found them **[run log]**.
- Pressing delivery day soon after issuing cut the driver rounds short. On the first live run, V2 went to a driver by default at round 2 before anyone could answer the higher pay **[run log]**. Delivery day now plays out the remaining rounds first, and a test covers it.

## Hardest case

Scarce butter requested by a retailer inferred to be a hotel, with no earlier bills for that pack, a light truck, and the order system failing mid-write. Butter stock is cut to a stand-in 60% of plan-day orders to create this case. Tests show the request needs a person's approval for that exact quantity, and that a write failure creates no duplicate order. `scripts/live_checks.py` drives the combined case, but no combined result is recorded here because its output was not saved.
