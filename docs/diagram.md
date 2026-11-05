# Trex architecture

The browser and CLI share the same agent and tools. This diagram describes the current prototype, including simulation boundaries.

```mermaid
flowchart TD
    Lead["Dispatch lead: request and exception decisions"] --> UI["Browser interface + API<br/>trex/web + server.py"]
    CLI["cli.py<br/>Auto-approves decisions in demo"] --> Runner
    UI --> Runner["orchestrator.py<br/>Wake on replies and resume runs"]
    Runner --> Agent["agent.py<br/>Saved Responses API tool loop"]
    Agent <--> Model["External model API<br/>Interprets requests and selects tools"]
    Agent --> Tools["tools.py<br/>Dispatch operations"]

    subgraph Planning["Planning and evidence inputs"]
        CSV["data/masked/invoice_lines.csv<br/>Masked billing history"] --> Ledger["ledger.py + normalize.py<br/>As-of view and unit parsing"]
        Ledger --> Belief["belief.py<br/>Stock-cover ranges, not observed stock"]
        Ledger --> Night["planning.py<br/>Truck board, priced options and offers"]
        Belief --> Night
        Assumptions["assumptions.json + standins.py<br/>Capacity, cost, stock, shelf life and type assumptions"] --> Night
        CSV -.-> Proxy["Explicit future-data proxies<br/>Next-day bills as orders<br/>Later bills as stock"]
        Proxy --> Night
        Night --> Solver["solver.py<br/>CP-SAT allocation, greedy fallback<br/>Then batch assignment"]
        Upstream["upstream.py<br/>Trace and vehicle matching<br/>All upstream data are stand-ins"]
    end

    Tools <--> Ledger
    Tools <--> Belief
    Tools <--> Night
    Tools <--> Upstream
    Tools --> Gate["gate.py<br/>Check numeric claims against evidence"]
    Gate --> Channels["channels.py<br/>Keyed outbox and inbox"]
    Tools --> Orders["orders_api.py<br/>Local test order system<br/>Read back after injected errors"]
    Tools -.-> Direct["Generated load-sheet notifications<br/>Do not pass through numeric gate"]
    Direct --> Channels

    subgraph Replies["Demonstration channels"]
        Channels <--> Phone["One team phone<br/>Replies, approvals, voice and photo<br/>No outside-party outreach"]
        Channels <--> Sim["Simulated retailer replies<br/>Use later billing history"]
        Tomorrow["run_tomorrow<br/>Simulated delivered and failed drops"]
    end

    Phone --> Runner
    Sim --> Runner
    UI --> Tomorrow
    CLI --> Tomorrow
    Tomorrow --> Runner
    Agent --> Drop["record_drop requires driver-message evidence<br/>log_miss records unresolved failures"]
    Drop --> Result["Saved decisions, orders, load sheets,<br/>drop records, misses and lead report"]

    Store[("store.py / SQLite<br/>Turns, evidence, events, decisions,<br/>inbox, outbox and test orders")]
    Agent <--> Store
    Tools <--> Store
    Channels <--> Store
    Orders <--> Store
    Store --> State["state.py<br/>Browser view of run and evidence"]
    State --> UI
```

## Boundaries that matter

- The model chooses tools and words. The allocation solver chooses allocated quantities. An offer or order request is not itself a delivered allocation.
- A person handles material ambiguity and policy exceptions. Some limits are enforced in tools; others remain agent instructions. The CLI supplies automatic approvals and cannot demonstrate independent exception resolution.
- The numeric gate checks numeric correspondence, not meaning or commercial truth. The diagram explicitly shows generated notifications that bypass that gate.
- Persisted turns and keyed writes support retries and restart recovery. External messaging is not a transactional, guaranteed-delivery system.
- Delivery proof is simulated apart from one live team phone. Even that live phone demonstrates evidence capture, not a verified commercial delivery.
- Capacity, cost, stock, shelf life, retailer types and upstream records are stand-ins. Future bills are used in explicit order/stock/reply proxies, so replay results are not leakage-free forecasts or measured savings.

The Mermaid source renders in a repository viewer that supports Mermaid. Use the public file URL as the submission's diagram link only after checking that it opens without signing in.
