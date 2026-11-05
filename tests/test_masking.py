"""The real brand must stay out of everything the repo publishes."""
import json
import subprocess
from pathlib import Path

import pytest

LOCAL = Path.home() / ".trex.mask.json"


def test_real_brand_is_nowhere_in_tracked_files():
    if not LOCAL.exists():
        pytest.skip("Local brand terms are only on the machine that masked the data")
    local = json.loads(LOCAL.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    for word in [local["audit_word"], *local.get("watch_words", [])]:
        found = subprocess.run(["git", "grep", "-il", word], cwd=root, capture_output=True, text=True).stdout.split()
        assert found == [], "a real name is in a tracked file"
