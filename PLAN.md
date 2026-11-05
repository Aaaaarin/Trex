# Trex: who does what tonight

**Goal:** by 6:00 AM, a public repo and a video where an unseen request becomes a truck sold full, with every fact sourced and each delivery proven.

## Working rules

- `main` only. Run `git pull --rebase` before every push, and push small commits often.
- Touch only the files you own below. Need a change elsewhere? Ask the other laptop.
- Never commit keys, raw sales files, the operator's document or real names. Use only `data/masked/`.
- Keys live in `~/.trex.env`, one per laptop, never in the repo.
- During the event, agents message only our own phones. No real retailers, transporters or companies.

## Laptop 1 (main)

Owns `trex/` except `trex/crustdata.py`, plus `tests/test_core.py`, `tests/test_safety.py` and `NOTES.md`.

| Time | Work | Done when |
|---|---|---|
| Now | Live agent run from the command line | A request reaches a cited load sheet |
| 12:30 | Priced options, sell to fill and source check in the live run | Handbook's first milestone passes |
| 2:15 | Telegram replies | A reply from the phone raises the load |
| 3:00 | Hardest case | Scarce stock, light truck, failing API, then proven drops |
| 3:30 | Dashboard joined up | Gauge climbs on screen |
| 4:15 | Chain trace and vehicle match in the live run | Butter traced from shelf to missing truck |
| 4:45 | Replay and test numbers | Results file written |
| 5:15 | Feature freeze | Fixes only |

## Laptop 2

Owns `trex/crustdata.py`, `tests/test_crustdata.py`, `tests/cases/`, `README.md`, `submission/` and the video.

| Order | Work | Done when |
|---|---|---|
| 1 | Crustdata transporter lookup: `find_transporters(lane, vehicle_type)` returns companies with name, domain, headcount, location and source URL. Use Company Search, then Company Identify, then Web Search. Return an empty list and a reason when nothing is found | Works on 5 Indian routes. Coverage written in `NOTES-crustdata.md`. Handles 402, 429 and empty results |
| 2 | Messy test set in `tests/cases/requests.json`: 30 requests with the expected retailer, product and outcome. Hinglish, misspellings, peti versus pack, no quantity, two products, unknown product, and reworded pairs that must reach the same decision. Use masked ids from `data/masked/` | File committed |
| 3 | `README.md` as the handbook asks: the service and who pays, what it does now, what comes next, how to run it, how it is built, approaches tried | Judge can run it from the README |
| 4 | Public high-level diagram (Excalidraw or similar, open to anyone) | Link in the README |
| 5 | Draft every form field in `submission/form.md` | Ready to paste |
| 6 | Loom account, and a test recording that plays in a private window | Confirmed |

## Both

| Time | Work |
|---|---|
| 5:15 | Freeze. Rehearse twice |
| 5:45 | Record the video |
| 6:00 | Check for real names, make the repo public, test a clean clone, first submission |
| 6:30 | Fix anything found, resubmit |
| 7:15 | Rehearse finals |

## Setup on another laptop

```
git clone https://github.com/Aaaaarin/Trex.git
cd Trex
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest tests -q
```

Create `~/.trex.env` with `CRUSTDATA_API_KEY=...`. Do not commit it.
