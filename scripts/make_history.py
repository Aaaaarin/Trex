"""Write data/standins/history.json: invented driver and depot history for the phone screens.

Nothing here is measured. The same seed gives the same file, and every total on screen is worked out from these rows.
Ratings set who gets offered first, so they are fixed per driver and only the rows around them vary.
"""
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLEET = json.loads((ROOT / "data" / "standins" / "fleet.json").read_text(encoding="utf-8"))
ASSUMPTIONS = json.loads((ROOT / "assumptions.json").read_text(encoding="utf-8"))
LAST_DAY = date.fromisoformat(ASSUMPTIONS["demo"]["plan_evening"])
DAYS = 30
DEPOT_DAYS = 14
DEPOT = "BR-1"
# driver: rating aimed for, chance of arriving on time, share of offers taken, how often they get picked
PROFILE = {
    "D02": (4.8, 0.96, 0.93, 9),
    "D04": (4.65, 0.92, 0.88, 7),
    "D01": (4.5, 0.9, 0.82, 6),
    "D03": (4.35, 0.88, 0.8, 6),
    "D06": (4.2, 0.84, 0.76, 4),
    "D08": (4.05, 0.82, 0.71, 3),
    "D07": (3.9, 0.78, 0.66, 3),
    "D05": (3.75, 0.74, 0.62, 3),
}
# truck: stops, kg, base pay as the stand-in formula gives it
TRUCKS = {"V1": ((26, 38), (2800, 3450), 4234), "V2": ((16, 24), (1900, 2700), 2350), "V3": ((12, 20), (1000, 1700), 2341)}
PAY_STEPS = (1.0, 1.0, 1.0, 1.15, 1.3)


def _stars(rng: random.Random, target: float, count: int) -> list[int]:
    """Whole stars that average as close to the target as this many trips can."""
    if target >= 4:
        fives = round((target - 4) * count)
        out = [5] * fives + [4] * (count - fives)
    else:
        threes = round((4 - target) * count)
        out = [3] * threes + [4] * (count - threes)
    rng.shuffle(out)
    return out


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def build(seed: int = 1016) -> dict:
    rng = random.Random(seed)
    limits = {d["id"]: d["max_kg"] for d in FLEET["drivers"]}
    report = int(FLEET["report_time"][:2]) * 60 + int(FLEET["report_time"][3:])
    days = [LAST_DAY - timedelta(days=i) for i in range(DAYS - 1, -1, -1)]
    trips: dict[str, list[dict]] = {d: [] for d in PROFILE}
    gates: list[dict] = []
    for day in days:
        if day.weekday() == 6:  # no dispatch on Sundays in this invented month
            gates.append({"date": day.isoformat(), "entries": []})
            continue
        used: set[str] = set()
        entries = []
        for slot, (truck, (stops, kg, base)) in enumerate(TRUCKS.items()):
            load = rng.randint(*kg)
            free = [d for d in PROFILE if d not in used and limits[d] >= load]
            driver = rng.choices(free, weights=[PROFILE[d][3] for d in free])[0]
            used.add(driver)
            on_time = rng.random() < PROFILE[driver][1]
            due = report + slot * FLEET["report_stagger_minutes"]
            arrive = due - rng.randint(0, 12) if on_time else due + rng.randint(8, 26)
            count = rng.randint(*stops)
            trips[driver].append({"date": day.isoformat(), "truck": truck, "stops": count, "kg": load, "pay_inr": round(base * rng.choice(PAY_STEPS)), "on_time": on_time, "late_minutes": 0 if on_time else arrive - due})
            entries.append({"truck": truck, "driver": driver, "report": _clock(due), "in": _clock(arrive), "out": _clock(arrive + rng.randint(20, 32)), "late_minutes": 0 if on_time else arrive - due, "pick_lines": rng.randint(count, count * 3)})
        gates.append({"date": day.isoformat(), "entries": entries})
    drivers = {}
    for driver, (target, _, taken, _) in PROFILE.items():
        rows = trips[driver]
        for row, stars in zip(rows, _stars(rng, target, len(rows))):
            row["stars"] = stars
        drivers[driver] = {"offers_seen": max(len(rows), round(len(rows) / taken)), "trips": rows}
    return {
        "_note": "Stand-ins. All of this is invented for the demo: no driver, trip, rating, earning or gate record here was measured. Run scripts/make_history.py to rebuild it.",
        "as_of": LAST_DAY.isoformat(),
        "drivers": drivers,
        "depots": {DEPOT: {"days": gates[-DEPOT_DAYS:]}},
    }


if __name__ == "__main__":
    path = ROOT / "data" / "standins" / "history.json"
    path.write_text(json.dumps(build(), indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {path}")
