# Build notes

Kept through the night: what we weighed, what worked, what did not.

## Approaches considered

| Approach | Promising because | Falls short because | Decision |
|---|---|---|---|
| Alerts dashboard from invoices | Fast, easy to demo | Leaves all the work with people | Rejected |
| Suggested orders for retailers | Clear value | Crowded market, needs retailer stock nobody has | Rejected |
| Late-order recovery desk | Bounded job | Treats a lost sale as recoverable, ignores truck economics | Replaced after operator feedback |
| LLM allocates stock | Quick to build | Arithmetic can be wrong and hard to audit | Rejected. Solver sets every quantity |
| Plant-to-branch load builder only | Matches the operator's 18 MT trucks | No real data at that level, and a tool already exists | Kept as upstream trace on labelled stand-ins |
| Nightly truck decision with sell to fill | Joins truck use, shelf life, range selling and lost sales | Retailer replies are unproven | **Chosen** |

## What worked

- Value divided by consumer price gives consistent quantities. Rate times quantity matches value on only about 60% of lines.
- Partial-first allocation with falling value per unit. The solver finishes the full night in about 1.3 seconds and gives the same answer each time.

- Crash recovery holds on the real server. Killed mid-run with 8 messages sent, it resumed by itself and finished with 177 distinct orders and no message sent twice (`scripts/crash_test.py`).
- With a 500 error after three order writes, it read each one back and wrote no duplicates.
- The source check blocked a real mistake: the agent worked out a 25 kg gap itself, and the check refused it because no tool had returned that number.
- The agent refused to guess between two 500 g butter packs and asked instead.

## What did not work

- Stock ranges are not yet better than a simple habit. On 144 frequently bought pairs, predicting a rebuy within 7 days scored 46.5%, the same as assuming everyone buys weekly, because the ranges flagged every pair as low. They are honest about uncertainty, but they do not rank well yet.
- Sell to fill has a low ceiling in the replay. 13 of 80 truck days reached 80% as billed. At most 40 could with offers, assuming every retailer says yes, so 40 is an upper limit.
- One offer could swallow a truck: a single shop was offered 648 kg. Offers are now capped per shop.

- The first version messaged 18 shops to gain 299 kg. Offers now use whole cases and start with larger buyers.
- The model first rejected tool calls with reasoning on Chat Completions, so the loop moved to the Responses API.

## Hardest case

Scarce butter requested by a hotel, a light truck and thin history together, with the order system failing mid-write. Stock is short, so the request needs a person. The truck still goes full on other products, and no order is duplicated.
