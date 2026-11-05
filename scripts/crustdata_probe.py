"""Live run of the transporter lookup. The lanes are test inputs, not the operator's real routes."""
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trex.crustdata import find_transporters  # noqa: E402

load_dotenv(Path.home() / ".trex.env")
LANES = ["Kanpur to Lucknow", "Kanpur to Delhi", "Lucknow to Varanasi", "Agra to Kanpur", "Delhi to Jaipur"]
KINDS = ["refrigerated", "dry"]
CREDIT_CEILING = 500


def balance() -> float:
    headers = {"authorization": f"Bearer {os.environ['CRUSTDATA_API_KEY']}", "x-api-version": "2025-11-01"}
    return httpx.get("https://api.crustdata.com/account/credits", headers=headers, timeout=20).json()["account"]["credits"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print("Test lanes only, not the operator's real routes. Nobody is contacted.")
    start = balance()
    for lane in LANES:
        for kind in KINDS:
            spent = start - balance()
            if spent > CREDIT_CEILING:
                print(f"Stopped: {spent:.2f} credits spent")
                return
            print(json.dumps(find_transporters(lane, kind), ensure_ascii=False, indent=1))
    print(f"Credits spent this run: {start - balance():.2f}")


if __name__ == "__main__":
    main()
