"""Paths, keys and the stand-in values every plan depends on."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RUNTIME = ROOT / "runtime"
RUNTIME.mkdir(exist_ok=True)
DB_PATH = Path(os.environ.get("TREX_DB", RUNTIME / "trex.sqlite"))

load_dotenv(Path.home() / ".trex.env", override=False)

MODEL = os.environ.get("TREX_MODEL", "gpt-6.1-sol")
ASSUMPTIONS = json.loads((ROOT / "assumptions.json").read_text(encoding="utf-8"))
DEMO = ASSUMPTIONS["demo"]


def standin(key: str):
    """Read a stand-in value, tagged so that callers can never present it as fact."""
    return ASSUMPTIONS["standins"][key]["value"]
