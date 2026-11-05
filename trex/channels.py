"""Messages to people. Live messages go only to the builder's own phone; everyone else is simulated.

Event rules forbid messaging real people outside the team, so simulated replies are labelled, and
they come from what each retailer really bought next, which the planner cannot see.
"""
import hashlib
import json
import os
import threading
import time

import httpx
import pandas as pd

from . import store
from .ledger import Ledger

YES = ["haan {n} bhej do", "ok {n} de dena", "{n} chahiye, kal bhejna", "theek hai {n}", "bhai {n} bhej dena, paisa gaadi pe"]
NO = ["abhi nahi", "stock hai abhi", "nahi chahiye", "next week dekhenge"]
SIM_DELAY_SECONDS = float(os.environ.get("TREX_SIM_DELAY", "4"))


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def live_roles(run: str) -> dict:
    return store.get_kv(run, "live", {})


def is_live(run: str, role: str, recipient: str) -> bool:
    live = live_roles(run)
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")) and live.get("chat_id") and (recipient in live.get("recipients", []) or role in live.get("roles", []))


def send(run: str, role: str, recipient: str, text: str, key: str, offer: dict | None = None) -> dict:
    """Send at most once per key, even if a crash or a retry calls this again."""
    if store.rows("select key from outbox where key=?", (key,)):
        store.log(run, "send_skipped_duplicate", key=key, recipient=recipient)
        return {"status": "already_sent", "key": key}
    channel = "telegram" if is_live(run, role, recipient) else "simulated"
    store.execute("insert into outbox(key, run, role, recipient, text, status, ts, channel, data) values(?,?,?,?,?,?,?,?,?)", (key, run, role, recipient, text, "queued", time.time(), channel, json.dumps(offer or {})))
    store.log(run, "message_sent", role=role, recipient=recipient, text=text, channel=channel, offer=offer)
    if channel == "telegram":
        _telegram_send(run, recipient, role, text)
        store.set_kv(run, "last_live", {"role": role, "recipient": recipient, "offer": offer})
    store.execute("update outbox set status='sent' where key=?", (key,))
    return {"status": "sent", "channel": channel, "key": key}


def _telegram_send(run: str, recipient: str, role: str, text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = live_roles(run)["chat_id"]
    label = f"[to {recipient}, {role}]\n{text}"
    httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": label}, timeout=15)


def simulate_reply(run: str, row: dict, evening: str, plan_date: str, distributor: str = "A") -> tuple[str, dict] | None:
    """A retailer's reply, drawn from their real later bills. Some never reply, as in life."""
    offer = json.loads(row["data"] or "{}")
    if row["role"] != "retailer" or not offer:
        return None
    if _h(run, row["recipient"], "silent") % 6 == 0:
        return None
    end = (pd.Timestamp(plan_date) + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    later = Ledger(evening, distributor)._future(end)
    bought = later[(later["retailer_id"] == row["recipient"]) & (later["group"] == offer.get("group"))]["pieces"].sum()
    if bought <= 0 and _h(run, row["recipient"], "yes") % 2:
        return NO[_h(row["recipient"]) % len(NO)], {"accepts": 0}
    n = int(offer["pieces"]) if bought <= 0 else max(1, min(int(offer["pieces"]), int(bought)))
    return YES[_h(row["recipient"], "p") % len(YES)].format(n=n), {"accepts": n}


class Simulator(threading.Thread):
    """Replies for retailers not on a live phone, a few seconds after each message."""

    def __init__(self, run: str, evening: str, plan_date: str, on_reply=None):
        super().__init__(daemon=True)
        self.run_id, self.evening, self.plan_date, self.on_reply = run, evening, plan_date, on_reply
        self.stopped = threading.Event()

    def run(self) -> None:
        while not self.stopped.wait(1.0):
            pending = store.rows("select * from outbox where run=? and channel='simulated' and status='sent' and ts<=?", (self.run_id, time.time() - SIM_DELAY_SECONDS))
            for row in pending:
                store.execute("update outbox set status='answered' where key=?", (row["key"],))
                reply = simulate_reply(self.run_id, row, self.evening, self.plan_date)
                if reply:
                    store.add_inbox(self.run_id, row["recipient"], row["role"], reply[0], True, f"sim:{row['key']}", {"offer": json.loads(row["data"] or "{}"), **reply[1]})
                    if self.on_reply:
                        self.on_reply()


class TelegramPoller(threading.Thread):
    """Reads the builder's own chat. The first message registers the chat for live roles."""

    def __init__(self, run: str, on_reply=None):
        super().__init__(daemon=True)
        self.run_id, self.on_reply = run, on_reply
        self.offset = 0

    def run(self) -> None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            return
        while True:
            try:
                r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"timeout": 20, "offset": self.offset}, timeout=30).json()
            except httpx.HTTPError:
                time.sleep(2)
                continue
            for update in r.get("result", []):
                self.offset = update["update_id"] + 1
                msg = update.get("message") or {}
                chat = msg.get("chat", {}).get("id")
                if not chat:
                    continue
                live = store.get_kv(self.run_id, "live", {})
                if not live.get("chat_id"):
                    live["chat_id"] = chat
                    store.set_kv(self.run_id, "live", live)
                last = store.get_kv(self.run_id, "last_live", {})
                text = msg.get("text") or msg.get("caption") or ("[photo]" if msg.get("photo") else "[voice note: transcription is not supported]" if msg.get("voice") else "")
                if text.startswith("/start"):
                    continue
                proof = {"photo": bool(msg.get("photo"))}
                store.add_inbox(self.run_id, last.get("recipient", "unknown"), last.get("role", "unknown"), text, False, f"tg:{update['update_id']}", {"offer": last.get("offer"), **proof})
                if self.on_reply:
                    self.on_reply()
