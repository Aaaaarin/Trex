import os
import sys
import tempfile
from pathlib import Path

# Every test run gets a fresh database, set before any trex module reads its config.
os.environ["TREX_DB"] = str(Path(tempfile.mkdtemp()) / "test.sqlite")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
