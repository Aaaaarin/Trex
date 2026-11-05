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

## What did not work

- Stock ranges are not yet better than a simple habit. On 144 frequently bought pairs, predicting a rebuy within 7 days scored 46.5%, the same as assuming everyone buys weekly, because the ranges flagged every pair as low. They are honest about uncertainty, but they do not rank well yet.
- Sell to fill has a low ceiling in the replay. 13 of 80 truck days reached 80% as billed. At most 40 could with offers, assuming every retailer says yes, so 40 is an upper limit.
- One offer could swallow a truck: a single shop was offered 648 kg. Offers are now capped per shop.

## Hardest case
