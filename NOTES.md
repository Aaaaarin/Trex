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

## What did not work

## Hardest case
