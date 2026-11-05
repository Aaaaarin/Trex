"""Messages to people. Live messages go only to the builder's own phone; everyone else is simulated.

Event rules forbid messaging real people outside the team, so simulated replies are labelled, and
they come from what each retailer really bought next, which the planner cannot see.
"""
import hashlib
import json
import logging
import os
import threading
import time
from urllib.parse import urlencode, urlsplit

import httpx
import openai
import pandas as pd

from . import store
from .ledger import Ledger

YES = ["haan {n} bhej do", "ok {n} de dena", "{n} chahiye, kal bhejna", "theek hai {n}", "bhai {n} bhej dena, paisa gaadi pe"]
NO = ["abhi nahi", "stock hai abhi", "nahi chahiye", "next week dekhenge"]
SIM_DELAY_SECONDS = float(os.environ.get("TREX_SIM_DELAY", "4"))
API = "https://api.telegram.org"
ALLOWED_UPDATES = json.dumps(["message", "callback_query"])
CALLBACK_BYTES = 64  # Telegram refuses longer callback data, so options travel as an index, never as text
log = logging.getLogger("trex.telegram")
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs each URL at INFO, and the URL holds the bot token


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


GLOBAL = "_global"


ON_SCREEN_SECONDS = 12


def mark_on_screen(role: str, who: str) -> None:
    """A phone screen for this person is open right now, so simulated stand-ins should leave them to it."""
    store.set_kv(GLOBAL, f"on_screen:{role}:{who}", time.time())


def on_screen(role: str, who: str) -> bool:
    return time.time() - (store.get_kv(GLOBAL, f"on_screen:{role}:{who}") or 0) < ON_SCREEN_SECONDS


def live_chat() -> int | None:
    known = store.get_kv(GLOBAL, "telegram_chat")
    if known:
        return known
    try:
        return int(os.environ["TELEGRAM_CHAT_ID"]) if os.environ.get("TELEGRAM_CHAT_ID") else None
    except ValueError:
        log.warning("TELEGRAM_CHAT_ID is not a number, so nothing goes to a phone")
        return None


def live_roles(run: str) -> dict:
    return {"chat_id": live_chat(), **store.get_kv(run, "live", {})}


def is_live(run: str, role: str, recipient: str) -> bool:
    live = live_roles(run)
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not live.get("chat_id"):
        return False
    fleet_roles = store.get_kv(run, "fleet_live_roles", [])
    if recipient in live.get("recipients", []) or role in live.get("roles", []) or role in fleet_roles:
        return True
    # One phone plays every role: "Use my phone" also brings the approver and the live driver's own drop messages.
    if role == "approver":
        return "dispatcher" in fleet_roles
    if role == "driver":
        driver = store.get_kv(run, "fleet_live_driver")
        return bool(driver) and _truck_driver(run, recipient) == driver
    return False


def send(run: str, role: str, recipient: str, text: str, key: str, offer: dict | None = None, buttons: list | None = None) -> dict:
    """Send at most once per key, even if a crash or a retry calls this again. A failed phone send is logged, never raised."""
    if store.rows("select key from outbox where key=?", (key,)):
        store.log(run, "send_skipped_duplicate", key=key, recipient=recipient)
        return {"status": "already_sent", "key": key}
    channel = "telegram" if is_live(run, role, recipient) else "simulated"
    store.execute("insert into outbox(key, run, role, recipient, text, status, ts, channel, data) values(?,?,?,?,?,?,?,?,?)", (key, run, role, recipient, text, "queued", time.time(), channel, json.dumps(offer or {})))
    store.log(run, "message_sent", role=role, recipient=recipient, text=text, channel=channel, offer=offer)
    status = "sent"
    if channel == "telegram":
        if _telegram_send(run, recipient, role, text, key, offer, buttons):
            store.set_kv(run, "last_live", {"role": role, "recipient": recipient, "offer": offer})
            if role == "driver" and (offer or {}).get("stop"):
                store.set_kv(run, "open_drop", {"recipient": recipient, "stop": offer["stop"]})
        else:
            status = "failed"
    store.execute("update outbox set status=? where key=?", (status, key))
    return {"status": status, "channel": channel, "key": key}


def _scrub(text: object) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    return str(text).replace(token, "[token]") if token else str(text)


def _api(method: str, payload: dict, timeout: float = 15) -> dict:
    """One Bot API call. It never raises, and what it returns never holds the token."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return {"ok": False, "description": "no bot token"}
    for attempt in (1, 2):
        try:
            body = httpx.post(f"{API}/bot{token}/{method}", json=payload, timeout=timeout).json()
        except (httpx.HTTPError, ValueError) as err:
            return {"ok": False, "description": type(err).__name__}
        if not isinstance(body, dict):
            return {"ok": False, "description": "unreadable reply"}
        wait = (body.get("parameters") or {}).get("retry_after", 0)
        if attempt == 1 and body.get("error_code") == 429 and wait <= 5:
            time.sleep(wait)
            continue
        break
    if not body.get("ok"):
        body["description"] = _scrub(body.get("description", "no description"))
    return body


def _truck_driver(run: str, recipient: str) -> str | None:
    """The driver holding truck V2, when a message is addressed to driver-V2."""
    if not recipient.startswith("driver-"):
        return None
    from . import fleet

    job = fleet.job_for(run, recipient.removeprefix("driver-"))
    return job.get("driver") if job else None


def _who(run: str, role: str, recipient: str) -> str:
    """Whose message this is, first on the line, because one phone shows every role."""
    if role in ("driver_live", "driver_pool"):
        return f"Driver {recipient}"
    if role == "driver":
        truck = recipient.removeprefix("driver-")
        driver = _truck_driver(run, recipient)
        return f"Driver {driver} (gaadi {truck})" if driver else f"Driver of gaadi {truck}"
    if role == "dispatcher":
        return "Dispatcher"
    if role == "approver":
        return "Approver"
    if role in ("retailer", "retailer_desk"):
        return f"Shop {recipient}"
    return f"{role} {recipient}"


def screen_url(run: str, role: str, recipient: str) -> str | None:
    """The person's own web screen. Only with a public address, as Telegram refuses a button pointing at this laptop."""
    base = (os.environ.get("TREX_PUBLIC_URL") or "").strip().rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.hostname in ("localhost", "127.0.0.1", "::1"):
        return None
    if role in ("driver_live", "driver_pool"):
        path, query = "/driver", {"id": recipient}
    elif role == "driver":
        driver = _truck_driver(run, recipient)
        if not driver:
            return None
        path, query = "/driver", {"id": driver}
    elif role in ("dispatcher", "approver"):
        path, query = "/gate", {}
    elif role in ("retailer", "retailer_desk"):
        path, query = "/call", {"shop": recipient}
    else:
        return None
    if os.environ.get("TREX_PUBLIC_KEY"):
        query["key"] = os.environ["TREX_PUBLIC_KEY"]
    return f"{base}{path}" + (f"?{urlencode(query)}" if query else "")


def _data(*parts) -> str:
    return "|".join(map(str, parts))


def offer_buttons(run: str, job: dict, driver: str) -> list:
    """Each button names the exact round, so an old offer at an old pay can never be accepted at a new one."""
    head = ("o", run, job["truck"], job["version"], job["round"], driver)
    return [[{"text": "Accept", "callback_data": _data(*head, "y")}, {"text": "Reject", "callback_data": _data(*head, "n")}]]


def gate_buttons(run: str, job: dict) -> list:
    head = ("g", run, job["truck"], job["version"], job["driver"])
    return [[{"text": "Gate in", "callback_data": _data(*head, "in")}, {"text": "Gate out", "callback_data": _data(*head, "out")}]]


def _automatic_buttons(run: str, role: str, recipient: str, key: str, offer: dict | None) -> list:
    """Buttons for messages sent from code that only knows the plain send: a decision and a drop."""
    if role == "approver" and ":decision:" in key:
        did = key.rsplit(":", 1)[1]
        found = store.rows("select options from decisions where id=? and run=?", (did, run))
        options = json.loads(found[0]["options"] or "[]") if found else []
        return [[{"text": str(o), "callback_data": _data("a", run, did, i)}] for i, o in enumerate(options) if str(o).strip()]
    if role == "driver" and (offer or {}).get("stop") and recipient.startswith("driver-"):
        head = ("s", run, recipient.removeprefix("driver-"), offer["stop"])
        return [[{"text": "Delivered", "callback_data": _data(*head, "d")}, {"text": "Failed", "callback_data": _data(*head, "f")}]]
    return []


def _keyboard(run: str, role: str, recipient: str, key: str, offer: dict | None, buttons: list | None) -> list:
    rows = [*(buttons or []), *_automatic_buttons(run, role, recipient, key, offer)]
    fitting = [[b for b in row if len(b["callback_data"].encode()) <= CALLBACK_BYTES] for row in rows]
    if fitting != rows:
        store.log(run, "telegram_button_dropped", key=key, why="Callback data too long. The typed reply still works")
    url = screen_url(run, role, recipient)
    return [row for row in fitting if row] + ([[{"text": "Open my screen", "url": url}]] if url else [])


def _telegram_send(run: str, recipient: str, role: str, text: str, key: str = "", offer: dict | None = None, buttons: list | None = None) -> bool:
    """True if Telegram took it. A refused button must not cost the message, so the message is retried without it."""
    try:
        rows = _keyboard(run, role, recipient, key, offer, buttons)
        payload = {"chat_id": live_roles(run)["chat_id"], "text": f"{_who(run, role, recipient)}: {text}"[:4096]}
        no_url = [[b for b in row if "url" not in b] for row in rows]
        no_url = [row for row in no_url if row]
        tries = [rows] + ([no_url] if no_url != rows else []) + ([[]] if rows and no_url else [])
        for keyboard in tries:
            reply = _api("sendMessage", {**payload, **({"reply_markup": {"inline_keyboard": keyboard}} if keyboard else {})})
            if reply.get("ok"):
                return True
            if reply.get("error_code") != 400:
                break  # only a refusal is safe to resend: after a timeout the message may already be on the phone
        store.log(run, "telegram_send_failed", key=key, role=role, recipient=recipient, error=reply.get("description"))
    except Exception as err:  # a phone failure must never break a run or a tool
        store.log(run, "telegram_send_failed", key=key, role=role, recipient=recipient, error=_scrub(type(err).__name__))
    return False


def _press(say: str, line: str | None = None, keep: tuple = (), wake: bool = False) -> dict:
    """What a button press did: a short note on the phone, a line added to the message, and which buttons stay."""
    return {"say": say, "line": line, "keep": keep, "wake": wake}


GONE = "Yeh offer ab khula nahi hai. Kuch nahi badla."


def _press_offer(run: str, truck: str, version: int, round_no: int, driver: str, accept: bool) -> dict:
    from . import fleet

    took = "Accepted: gaadi {truck} aapki, report {report} (stand-in), pay Rs {pay} (stand-in)."
    with fleet._lock:
        job = store.get_kv(run, fleet._key(truck, version))
        if job and job["status"] == "assigned" and job["driver"] == driver:
            line = took.format(truck=truck, report=job["report_time"], pay=job["pay"])
            return _press(f"Gaadi {truck} pehle se aapki hai. Kuch nahi badla.", line)
        if not job or job["status"] != "open" or driver not in job["asked"] or not fleet._plan_live(run, job):
            return _press(GONE, GONE)
        if job["round"] != round_no:
            note = "Yeh purana offer hai, pay badal chuka hai. Naya message dekho. Kuch nahi badla."
            return _press(note, note)
        said = next((r["answer"] for r in job["replies"] if r["driver"] == driver and r["round"] == round_no and r["answer"] in ("yes", "no")), None)
        if said:
            word = "Accept" if said == "yes" else "Reject"
            return _press(f"Aap pehle hi {word} kar chuke hain. Kuch nahi badla.", f"{word}: gaadi {truck}.")
        result = fleet.driver_reply(run, driver, "haan" if accept else "nahi", False, truck)
        if "error" in result:
            note = f"Nahi hua: {driver} ke paas pehle se ek gaadi hai." if "already" in result["error"] else GONE
            return _press(note, note)
        if not accept:
            return _press(f"Reject ho gaya: gaadi {truck}.", f"Reject: gaadi {truck}.")
        if result.get("driver") != driver:
            note = f"Gaadi {truck} ka plan badal gaya, yeh kaam cancel hai."
            return _press(note, note)
        job = store.get_kv(run, fleet._key(truck, version))
        return _press(f"Gaadi {truck} aapki.", took.format(truck=truck, report=job["report_time"], pay=job["pay"]))


def _press_gate(run: str, truck: str, version: int, driver: str, event: str) -> dict:
    from . import fleet

    with fleet._lock:
        job = fleet.job_for(run, truck)
        if not job or job["version"] != version or job["status"] != "assigned" or job["driver"] != driver:
            note = f"Gaadi {truck} ka yeh kaam ab chal nahi raha. Kuch log nahi hua."
            return _press(note, note)
        result = fleet.log_gate(run, truck, event, by="phone")
    if "error" in result:
        return _press(result["error"], result["error"])
    other = "out" if event == "in" else "in"
    keep = (_data("g", run, truck, version, driver, other),) if other not in result["gate"] else ()
    clock = result["gate"][event]["clock"]
    line = f"Gate {event} logged: gaadi {truck}, driver {driver}, {clock} (demo clock)."
    if result["status"] == "already_logged":
        return _press(f"Gate {event} pehle hi logged hai: {clock}. Kuch nahi badla.", line, keep)
    return _press(f"Gate {event} logged: {clock}.", line, keep)


def _press_drop(run: str, truck: str, retailer: str, delivered: bool) -> dict:
    """Says only what the driver said. It closes nothing and claims no photo: the agent still closes the stop with
    record_drop on this message, exactly as for the driver's screen, and only a real photo sets the photo flag."""
    from . import dispatch

    closed = store.get_kv(run, "drops", {}).get(retailer)
    if closed:
        note = f"Stop {retailer} pehle hi band hai: {closed['status']}. Kuch nahi badla."
        return _press(note, note)
    plan = dispatch.current_plan(run, truck)
    if not plan or plan["status"] != "issued" or retailer not in {row["retailer"] for row in store.get_kv(run, f"sheet:{truck}", [])}:
        note = f"Stop {retailer} ab gaadi {truck} par nahi hai. Kuch nahi badla."
        return _press(note, note)
    sender = f"driver-{truck}"
    text = f"{retailer} delivered" if delivered else f"{retailer} dukan band thi, maal wapas"
    if not store.add_inbox(run, sender, "driver", text, False, f"driver-screen:{retailer}", {"retailer": retailer, "photo": False}):
        note = f"Stop {retailer} ka jawab pehle hi ja chuka hai. Kuch nahi badla."
        return _press(note, note)
    if delivered:
        store.set_kv(run, "open_drop", {"recipient": sender, "stop": retailer})
        return _press("Delivered note ho gaya.", f"Delivered: {retailer}, driver ka message. Photo abhi nahi aayi. Photo bhejoge to isi stop par lagegi.", wake=True)
    if (store.get_kv(run, "open_drop") or {}).get("stop") == retailer:
        store.set_kv(run, "open_drop", None)
    last = store.get_kv(run, "last_live", {})
    if (last.get("offer") or {}).get("stop") == retailer:
        store.set_kv(run, "last_live", {**last, "offer": None})
    return _press("Failed note ho gaya.", f"Failed: {retailer}, dukan band thi, maal wapas.", wake=True)


def _press_decision(run: str, decision: str, index: int) -> dict:
    """The answer saved is the option itself, read back from the decision, so approval scope checks see the exact words."""
    from . import orchestrator

    query = "select answer, options from decisions where id=? and run=?"
    found = store.rows(query, (decision, run))
    if not found:
        return _press("Yeh sawaal ab nahi hai. Kuch nahi badla.", "Yeh sawaal ab nahi hai.")
    if found[0]["answer"] is not None:
        return _press(f"Pehle hi jawab ho chuka: {found[0]['answer']}. Kuch nahi badla."[:200], f"Answer: {found[0]['answer']}")
    options = json.loads(found[0]["options"] or "[]")
    if not 0 <= index < len(options):
        return _press("Yeh button ab sahi nahi hai. Kuch nahi badla.")
    orchestrator.answer_decision(run, decision, options[index], "phone")
    answer = store.rows(query, (decision, run))[0]["answer"]
    return _press(f"Answer: {answer}"[:200], f"Answer: {answer}")


def _act(data: str, latest_run) -> dict:
    parts = data.split("|")
    kind, run = (parts + [""])[:2]
    if run != latest_run():
        note = "Yeh purane run ka button hai. Kuch nahi badla."
        return _press(note, note)
    if kind == "o" and len(parts) == 7:
        return _press_offer(run, parts[2], int(parts[3]), int(parts[4]), parts[5], parts[6] == "y")
    if kind == "g" and len(parts) == 6 and parts[5] in ("in", "out"):
        return _press_gate(run, parts[2], int(parts[3]), parts[4], parts[5])
    if kind == "s" and len(parts) == 5:
        return _press_drop(run, parts[2], parts[3], parts[4] == "d")
    if kind == "a" and len(parts) == 4:
        return _press_decision(run, parts[2], int(parts[3]))
    return _press("Yeh button ab sahi nahi hai. Kuch nahi badla.")


def handle_callback(callback: dict, latest_run, on_reply=None) -> None:
    """One button press. It always answers, so the phone stops waiting, and a repeat or a stale press changes nothing."""
    msg = callback.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    data = str(callback.get("data") or "")
    if not chat or chat != live_chat():
        _api("answerCallbackQuery", {"callback_query_id": callback.get("id"), "text": "Yeh button is chat ke liye nahi hai."})
        return
    try:
        press = _act(data, latest_run)
    except Exception as err:  # a bad press must not stop the poller or leave the phone waiting
        log.warning("Button press failed: %s", _scrub(type(err).__name__))
        press = _press("Abhi nahi hua. Dobara dabao, ya command likho.")
    _api("answerCallbackQuery", {"callback_query_id": callback.get("id"), "text": press["say"][:200]})
    if press["line"]:
        text = msg.get("text") or ""
        if press["line"] not in text:
            text = f"{text}\n\n{press['line']}"[:4096]
        rows = [[b for b in row if "url" in b or b.get("callback_data") in press["keep"]] for row in (msg.get("reply_markup") or {}).get("inline_keyboard", [])]
        edit = _api("editMessageText", {"chat_id": chat, "message_id": msg.get("message_id"), "text": text, "reply_markup": {"inline_keyboard": [row for row in rows if row]}})
        if not edit.get("ok") and "not modified" not in str(edit.get("description")):
            log.warning("Could not edit the message: %s", edit.get("description"))
    if press["wake"] and on_reply:
        on_reply(data.split("|")[1])


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


def pending_live_stop(run: str, role: str | None) -> str | None:
    """The driver's own stop still open, once the delivery day has begun. A later agent message must not lose it."""
    if role != "driver" or not store.get_kv(run, "delivery_started"):
        return None
    closed = store.get_kv(run, "drops", {})
    for retailer in store.get_kv(run, "live", {}).get("recipients", []):
        if retailer not in closed:
            return retailer
    return None


def _transcribe(token: str, file_id: str) -> str:
    """Turn a voice note into text. If that fails, say so, and the agent treats it as unclear."""
    try:
        path = httpx.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id}, timeout=20).json()["result"]["file_path"]
        audio = httpx.get(f"https://api.telegram.org/file/bot{token}/{path}", timeout=60).content
        client = openai.OpenAI(api_key=os.environ["CHATGPT_API_KEY"], timeout=60)
        return client.audio.transcriptions.create(model="gpt-4o-transcribe", file=("note.ogg", audio)).text
    except (httpx.HTTPError, KeyError, openai.OpenAIError) as err:
        return f"(could not transcribe: {type(err).__name__})"


class TelegramPoller(threading.Thread):
    """Reads the builder's own chat for the most recent run. The first message registers the chat. Button presses
    arrive here too, so one poller is all that ever reads this bot."""

    def __init__(self, latest_run, on_reply=None):
        super().__init__(daemon=True)
        self.latest_run, self.on_reply = latest_run, on_reply
        self.offset = 0

    def run(self) -> None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            return
        while True:
            self.poll_once(token)

    def poll_once(self, token: str) -> None:
        try:
            r = httpx.get(f"{API}/bot{token}/getUpdates", params={"timeout": 20, "offset": self.offset, "allowed_updates": ALLOWED_UPDATES}, timeout=30).json()
        except (httpx.HTTPError, ValueError):
            time.sleep(2)
            return
        if not isinstance(r, dict) or not r.get("ok"):
            # A second poller on this bot gets a refusal straight away, so without this wait the loop would spin.
            log.warning("getUpdates refused: %s", _scrub(r.get("description") if isinstance(r, dict) else "unreadable reply"))
            time.sleep(5)
            return
        for update in r.get("result", []):
            self.offset = update["update_id"] + 1
            try:
                self.handle(update, token)
            except Exception as err:  # one bad update, such as an unknown shop, must not end all phone replies
                log.warning("Update %s failed: %s", update["update_id"], _scrub(type(err).__name__))

    def handle(self, update: dict, token: str) -> None:
        if update.get("callback_query"):
            handle_callback(update["callback_query"], self.latest_run, self.on_reply)
            return
        msg = update.get("message") or {}
        chat = msg.get("chat", {}).get("id")
        if not chat:
            return
        known = store.get_kv(GLOBAL, "telegram_chat")
        if known and chat != known:  # the bot is public, so the first chat keeps the alerts and strangers are ignored
            return
        store.set_kv(GLOBAL, "telegram_chat", chat)
        run = self.latest_run()
        text = msg.get("text") or msg.get("caption") or ("[photo]" if msg.get("photo") else "")
        if msg.get("voice"):
            text = "[voice note] " + _transcribe(token, msg["voice"]["file_id"])
        if text.startswith("/start"):
            return
        from . import fleet, phone  # imported here because both send through this module

        shop = text.strip().split(maxsplit=2)
        if len(shop) == 3 and shop[0].lower() == "/shop" and phone.phone_shop_order(shop[1].upper(), shop[2], f"tg:{update['update_id']}"):
            return
        if not run:
            return

        if fleet.route_phone(run, text, bool(msg.get("photo")), update["update_id"]):
            return
        last = store.get_kv(run, "last_live", {})
        drop = store.get_kv(run, "open_drop") if msg.get("photo") else None
        if drop:  # a photo belongs to the driver's open drop, even if a message to another role came after the ask
            last = {"role": "driver", "recipient": drop["recipient"], "offer": {"stop": drop["stop"]}}
        offer = last.get("offer") or {}
        stop = offer.get("stop") or pending_live_stop(run, last.get("role"))
        if last.get("role") == "approver" and not msg.get("photo"):
            open_decision = store.rows("select id from decisions where run=? and answer is null order by ts desc limit 1", (run,))
            if open_decision:
                store.execute("update decisions set answer=?, answered_by=? where id=?", (text, "phone", open_decision[0]["id"]))
                store.log(run, "decision_answered", id=open_decision[0]["id"], answer=text, by="phone")
                if self.on_reply:
                    self.on_reply(run)
                return
        if stop and msg.get("photo"):
            text = f"Driver sent a delivery photo for stop {stop}. {msg.get('caption') or ''}".strip()
        elif stop:
            text = f"{text} (about stop {stop})"
        store.add_inbox(run, last.get("recipient", "unknown"), last.get("role", "unknown"), text, False, f"tg:{update['update_id']}", {"offer": last.get("offer"), "photo": bool(msg.get("photo")), "retailer": stop or last.get("recipient")})
        if self.on_reply:
            self.on_reply(run)
