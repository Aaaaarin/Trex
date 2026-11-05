"""Source check. Code, not the model, decides whether a claim may leave the system.

Every number in outgoing text must match a claim, and every claim must match the evidence it cites.
"""
import re

from . import store

NUM = re.compile(r"\d+(?:[.,]\d+)*")
IDENTIFIER = re.compile(r"\b(?:R-\d+|R\d{2}|V\d+|P\d{3}|S\d{2}|E\d+|B-[0-9A-F]+|INV-[0-9A-F]+|T\d+)\b")
TIME = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:baje|am|pm|AM|PM)\b")


class Blocked(Exception):
    pass


def _numbers(value) -> list[float]:
    found: list[float] = []
    if isinstance(value, bool):
        return found
    if isinstance(value, (int, float)):
        found.append(float(value))
    elif isinstance(value, str):
        for n in NUM.findall(value):
            plain = n.replace(",", "")
            if plain.replace(".", "", 1).isdigit():
                found.append(float(plain))
    elif isinstance(value, dict):
        for v in value.values():
            found += _numbers(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            found += _numbers(v)
    return found


def _matches(value: float, sources: list[float]) -> bool:
    for s in sources:
        if abs(s - value) <= max(0.005 * abs(s), 0.001):
            return True
        if round(s) == value or round(s * 100) == value or round(s, 1) == value:
            return True
    return False


def text_numbers(text: str) -> list[float]:
    clean = TIME.sub(" ", IDENTIFIER.sub(" ", text))
    out = []
    for n in NUM.findall(clean):
        plain = n.replace(",", "")
        if plain.replace(".", "", 1).isdigit():
            out.append(float(plain))
    return out


def check(run: str, text: str, claims: list[dict]) -> dict:
    """Return whether stand-ins were used. Raise Blocked with the exact reason otherwise."""
    used_standin = False
    claimed: list[float] = []
    for claim in claims:
        value = float(claim["value"])
        evidence_ids = claim.get("evidence_ids") or []
        if not evidence_ids:
            raise Blocked(f"Claim {value:g} cites no evidence")
        sources: list[float] = []
        for eid in evidence_ids:
            e = store.get_evidence(eid)
            if e is None or e["run"] != run:
                raise Blocked(f"Evidence {eid} does not exist")
            used_standin = used_standin or bool(e["standin"])
            sources += _numbers(e["data"])
        if not _matches(value, sources):
            raise Blocked(f"Claim {value:g} does not match its evidence {evidence_ids}")
        claimed.append(value)
    for number in text_numbers(text):
        if not any(abs(number - c) <= 0.005 * max(abs(c), 1) for c in claimed):
            raise Blocked(f"The number {number:g} in the text has no claim. Add a claim with evidence, or remove the number")
    store.log(run, "gate_passed", claims=len(claims), used_standin=used_standin)
    return {"used_standin": used_standin}


def record_block(run: str, text: str, reason: str) -> None:
    store.log(run, "gate_blocked", reason=reason, text=text[:300])
