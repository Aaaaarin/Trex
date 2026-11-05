"""Telegram buttons through the one poller, on a fake Bot API. Nothing here reaches the network."""
import json

import httpx
import pytest

from trex import channels, dispatch, fleet, orchestrator, store
from trex.tools import Tools

EVENING, DAY = "2026-10-15", "2026-10-16"
TOKEN = "123456:FAKE-TOKEN-FOR-TESTS"
CHAT = 555
_n = [0]


class FakeTelegram:
    """Stands in for httpx.post and httpx.get. It remembers each call and keeps what each message would show."""

    def __init__(self):
        self.calls, self.updates, self.refuse = [], [], []
        self.gets = []
        self.seq = 100
        self.next_update = 1

    def post(self, url, json=None, timeout=None):
        assert url.startswith(f"https://api.telegram.org/bot{TOKEN}/")
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, json))
        if self.refuse:
            reply = self.refuse.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        self.seq += 1
        return httpx.Response(200, json={"ok": True, "result": {"message_id": self.seq}})

    def get(self, url, params=None, timeout=None):
        assert url.endswith("/getUpdates")
        self.gets.append(params)
        updates, self.updates = self.updates, []
        return httpx.Response(200, json={"ok": True, "result": updates})

    def sent(self):
        return [body for method, body in self.calls if method == "sendMessage"]

    def edits(self):
        return [body for method, body in self.calls if method == "editMessageText"]

    def answers(self):
        return [body["text"] for method, body in self.calls if method == "answerCallbackQuery"]

    def message(self, starts):
        return next(m for m in self.sent() if m["text"].startswith(starts))

    def push(self, **update):
        self.updates.append({"update_id": self.next_update, **update})
        self.next_update += 1


def labels(message):
    return [b["text"] for row in message.get("reply_markup", {}).get("inline_keyboard", []) for b in row]


def press(tg, poller, message, label, chat=CHAT):
    """Press a button on a message as Telegram would deliver it, through the poller."""
    button = next(b for row in message["reply_markup"]["inline_keyboard"] for b in row if b["text"] == label)
    tg.push(callback_query={"id": f"cb{tg.next_update}", "data": button["callback_data"], "message": {"message_id": 1, "chat": {"id": chat}, "text": message["text"], "reply_markup": message["reply_markup"]}})
    poller.poll_once(TOKEN)
    return tg.answers()[-1]


@pytest.fixture
def tg(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.delenv("TREX_PUBLIC_URL", raising=False)
    monkeypatch.delenv("TREX_PUBLIC_KEY", raising=False)
    monkeypatch.setattr(channels.httpx, "post", fake.post)
    monkeypatch.setattr(channels.httpx, "get", fake.get)
    monkeypatch.setattr(channels.time, "sleep", lambda s: None)
    store.set_kv("_global", "telegram_chat", CHAT)
    yield fake
    store.execute("delete from kv where run='_global' and key='telegram_chat'")


@pytest.fixture
def run(tg):
    _n[0] += 1
    name = f"tg{_n[0]}"
    store.create_run(name, "test")
    store.set_kv(name, "fleet_live_driver", "D03")
    store.set_kv(name, "fleet_live_roles", ["driver_live", "dispatcher"])
    return name


@pytest.fixture
def poller(run):
    return channels.TelegramPoller(lambda: run)


def approve(run, kind, **scope):
    full = {"kind": kind, "truck": "", "retailer": "", "product": "", "pieces": 0, "detail": "", "basis": dispatch.basis_of(run, scope.get("truck") or None), **scope}
    did = f"{run}-{kind}-{len(store.rows('select id from decisions where run=?', (run,)))}"
    store.execute("insert into decisions(id, run, ts, question, options, answer, answered_by, evidence, scope) values(?,?,?,?,?,?,?,?,?)", (did, run, 0, "q", "[]", "Approve", "test", "[]", json.dumps(full)))


def issue(run, truck="V3"):
    tools = Tools(run, EVENING, DAY)
    approve(run, "under_target", truck=truck, pieces=tools.preview_plan(truck)["kg"])
    assert tools.freeze_plan(truck, "go", "test")["status"] == "frozen"
    tools.write_orders(truck)
    assert tools.issue_load_sheet(truck).get("stops")
    return tools


def offered(run, tg):
    """A real issued plan with its offer on the fake phone."""
    tools = issue(run)
    fleet.tick(run, 1000)
    assert "D03" in fleet.job_for(run, "V3")["asked"], "The live driver must get the offer"
    return tools, tg.message("Driver D03:")


def assigned(run, tg, poller):
    tools, offer = offered(run, tg)
    assert press(tg, poller, offer, "Accept") == "Gaadi V3 aapki."
    return tools, tg.message("Dispatcher:")


def test_poller_asks_for_messages_and_button_presses(tg, poller):
    poller.poll_once(TOKEN)
    assert json.loads(tg.gets[0]["allowed_updates"]) == ["message", "callback_query"]


def test_offer_says_who_and_shows_truck_stops_kg_pay_report_and_two_buttons(run, tg):
    _, offer = offered(run, tg)
    job = fleet.job_for(run, "V3")
    assert offer["text"].startswith("Driver D03: Naya kaam: gaadi V3")
    for fact in (f"{job['stops']} stop", f"{job['kg']} kg", f"Rs {job['pay']}", f"Report {fleet.CONFIG['report_time']}"):
        assert fact in offer["text"]
    assert labels(offer) == ["Accept", "Reject"]
    assert offer["chat_id"] == CHAT
    assert all(len(b["callback_data"].encode()) <= 64 for row in offer["reply_markup"]["inline_keyboard"] for b in row)
    assert len(tg.sent()) == 1, "Simulated drivers get nothing on the phone"


def test_accept_takes_the_job_answers_the_press_and_edits_the_message(run, tg, poller):
    _, offer = offered(run, tg)
    assert press(tg, poller, offer, "Accept") == "Gaadi V3 aapki."
    job = fleet.job_for(run, "V3")
    assert job["driver"] == "D03" and job["how"] == "accepted"
    assert job["replies"][-1]["simulated"] is False
    edit = tg.edits()[-1]
    assert "Accepted" in edit["text"] and job["report_time"] in edit["text"] and offer["text"] in edit["text"]
    assert edit["reply_markup"] == {"inline_keyboard": []}


def test_a_second_accept_changes_nothing_and_says_so(run, tg, poller):
    _, offer = offered(run, tg)
    press(tg, poller, offer, "Accept")
    before = fleet.job_for(run, "V3")
    count = len(tg.sent())
    assert "Kuch nahi badla" in press(tg, poller, offer, "Accept")
    assert fleet.job_for(run, "V3") == before and len(tg.sent()) == count


def test_reject_then_accept_on_the_same_offer_does_not_take_the_job(run, tg, poller):
    _, offer = offered(run, tg)
    assert "Reject" in press(tg, poller, offer, "Reject")
    assert "Reject" in tg.edits()[-1]["text"]
    assert "Kuch nahi badla" in press(tg, poller, offer, "Accept")
    job = fleet.job_for(run, "V3")
    assert job["status"] == "open" and [r["answer"] for r in job["replies"] if r["driver"] == "D03"] == ["no"]


def test_an_old_offer_cannot_be_accepted_after_the_pay_changed(run, tg, poller):
    _, offer = offered(run, tg)
    job = fleet.job_for(run, "V3")
    job.update({"round": job["round"] + 1, "pay": job["pay"] + 50})
    fleet._save(run, job)
    assert "purana offer" in press(tg, poller, offer, "Accept")
    assert fleet.job_for(run, "V3")["status"] == "open"


def test_an_offer_someone_else_took_is_refused_and_sends_nothing_new(run, tg, poller):
    _, offer = offered(run, tg)
    other = next(d for d in fleet.job_for(run, "V3")["asked"] if d != "D03")
    fleet.driver_reply(run, other, "haan", now=1001)
    count = len(tg.sent())
    assert "khula nahi" in press(tg, poller, offer, "Accept")
    assert fleet.job_for(run, "V3")["driver"] == other and len(tg.sent()) == count


def test_an_offer_on_a_held_truck_is_refused(run, tg, poller):
    _, offer = offered(run, tg)
    store.set_kv(run, "truck_status:V3", "held")
    assert "khula nahi" in press(tg, poller, offer, "Accept")
    assert fleet.job_for(run, "V3")["driver"] is None


def test_button_from_an_older_run_does_nothing(run, tg, poller):
    _, offer = offered(run, tg)
    newer = channels.TelegramPoller(lambda: "a-newer-run")
    assert "purane run" in press(tg, newer, offer, "Accept")
    assert fleet.job_for(run, "V3")["driver"] is None


def test_button_from_another_chat_does_nothing(run, tg, poller):
    _, offer = offered(run, tg)
    assert "is chat" in press(tg, poller, offer, "Accept", chat=999)
    assert fleet.job_for(run, "V3")["driver"] is None and not tg.edits()
    assert store.get_kv("_global", "telegram_chat") == CHAT


def test_dispatcher_gets_the_pick_list_with_gate_buttons(run, tg, poller):
    _, note = assigned(run, tg, poller)
    picks = fleet._pick_list(run, "V3")
    assert note["text"].startswith("Dispatcher: Kal gaadi V3: driver D03")
    assert picks[0]["product_name"] in note["text"] and str(picks[0]["pieces"]) in note["text"]
    assert labels(note) == ["Gate in", "Gate out"]


def test_gate_buttons_log_once_and_leave_the_other_button(run, tg, poller):
    _, note = assigned(run, tg, poller)
    assert "logged" in press(tg, poller, note, "Gate in")
    job = fleet.job_for(run, "V3")
    assert job["gate"]["in"]["by"] == "phone" and job["gate"]["in"]["simulated"] is False
    assert labels(tg.edits()[-1]) == ["Gate out"]
    assert "Gate in logged" in tg.edits()[-1]["text"]
    assert "Kuch nahi badla" in press(tg, poller, note, "Gate in")
    assert fleet.job_for(run, "V3")["gate"] == job["gate"]
    assert not fleet.may_roll(run, "V3")
    press(tg, poller, note, "Gate out")
    assert labels(tg.edits()[-1]) == []
    assert fleet.may_roll(run, "V3")


def test_gate_button_for_a_replaced_driver_logs_nothing(run, tg, poller):
    _, note = assigned(run, tg, poller)
    fleet.no_show(run, "V3", now=2000)
    assert "chal nahi raha" in press(tg, poller, note, "Gate in")
    assert fleet.job_for(run, "V3")["gate"] == {}


def on_the_road(run, tg, poller):
    """D03 has the truck, it has left, and one live stop is waiting on the phone."""
    tools, note = assigned(run, tg, poller)
    press(tg, poller, note, "Gate in")
    press(tg, poller, note, "Gate out")
    retailer = store.get_kv(run, "sheet:V3")[0]["retailer"]
    store.set_kv(run, "live", {"recipients": [retailer], "roles": []})
    orchestrator.run_tomorrow(run)
    return tools, retailer, tg.message("Driver D03 (gaadi V3): Drop at")


def proof(run, seq, row):
    data = json.loads(row["data"])
    return store.add_evidence(run, "message", f"inbox:{seq}", "drop", {"sender": row["sender"], "role": row["role"], "text": row["text"], "retailer": data.get("retailer"), "photo": data.get("photo", False)})


def test_delivered_button_is_only_the_drivers_message_and_never_a_photo(run, tg, poller):
    tools, retailer, ask = on_the_road(run, tg, poller)
    assert labels(ask) == ["Delivered", "Failed"]
    assert "photo bhejo" in ask["text"]
    assert retailer not in store.get_kv(run, "drops", {}), "Asking closes nothing"
    press(tg, poller, ask, "Delivered")
    assert retailer not in store.get_kv(run, "drops", {}), "The button closes nothing by itself"
    row = next(r for r in store.unhandled_inbox(run) if r["sender"] == "driver-V3" and not r["simulated"])
    assert json.loads(row["data"]) == {"retailer": retailer, "photo": False} and row["text"] == f"{retailer} delivered"
    closed = tools.record_drop("V3", retailer, "delivered", proof(run, row["seq"], row))
    assert closed["closed"] == retailer
    assert store.get_kv(run, "drops")[retailer]["photo"] is False


def test_second_drop_press_cannot_change_the_first_answer(run, tg, poller):
    _, retailer, ask = on_the_road(run, tg, poller)
    press(tg, poller, ask, "Delivered")
    assert "Kuch nahi badla" in press(tg, poller, ask, "Failed")
    rows = [r for r in store.unhandled_inbox(run) if r["sender"] == "driver-V3" and not r["simulated"]]
    assert [r["text"] for r in rows] == [f"{retailer} delivered"]


def test_failed_button_then_a_closed_stop_is_refused(run, tg, poller):
    tools, retailer, ask = on_the_road(run, tg, poller)
    press(tg, poller, ask, "Failed")
    row = next(r for r in store.unhandled_inbox(run) if r["sender"] == "driver-V3" and not r["simulated"])
    assert "dukan band" in row["text"]
    tools.record_drop("V3", retailer, "failed", proof(run, row["seq"], row))
    assert "pehle hi band" in press(tg, poller, ask, "Delivered")
    assert store.get_kv(run, "drops")[retailer]["status"] == "failed"


def test_photo_reaches_the_open_stop_as_the_drivers_message_after_a_message_to_a_shop(run, tg, poller):
    tools, retailer, ask = on_the_road(run, tg, poller)
    channels.send(run, "retailer", retailer, "Aapka maal aa raha hai", f"{run}:later-shop-message")
    assert store.get_kv(run, "last_live")["role"] == "retailer"
    tg.push(message={"chat": {"id": CHAT}, "photo": [{"file_id": "x"}]})
    poller.poll_once(TOKEN)
    row = next(r for r in store.unhandled_inbox(run) if "delivery photo" in r["text"])
    assert row["sender"] == "driver-V3" and row["role"] == "driver" and retailer in row["text"]
    tools.record_drop("V3", retailer, "delivered", proof(run, row["seq"], row))
    assert store.get_kv(run, "drops")[retailer]["photo"] is True


def decision(run, tools=None, options=("Approve under target", "Hold the truck")):
    tools = tools or Tools(run, EVENING, DAY)
    question = "Truck V3 target se kam hai. Kya karein?"
    store.execute("insert into decisions(id, run, ts, question, options, scope) values(?,?,?,?,?,?)", (f"{run}dec", run, 1, question, json.dumps(list(options)), "{}"))
    channels.send(run, "approver", "approver", question + "\nOptions: " + ", ".join(options), f"{run}:decision:{run}dec")
    return f"{run}dec"


def answer_of(did):
    row = store.rows("select answer, answered_by from decisions where id=?", (did,))[0]
    return row["answer"], row["answered_by"]


def test_decision_gets_a_button_per_option_and_saves_the_exact_option(run, tg, poller):
    long_option = "Approve under target for truck V3 at this exact load, as previewed and shown on the plan screen"
    did = decision(run, options=(long_option, "Hold the truck"))
    ask = tg.message("Approver:")
    assert labels(ask) == [long_option, "Hold the truck"]
    assert all(len(b["callback_data"].encode()) <= 64 for row in ask["reply_markup"]["inline_keyboard"] for b in row)
    press(tg, poller, ask, long_option)
    assert answer_of(did) == (long_option, "phone")
    assert long_option in tg.edits()[-1]["text"] and labels(tg.edits()[-1]) == []
    approved = store.rows("select id from decisions where id=? and lower(answer) like 'approve%'", (did,))
    assert approved, "The approval check reads the words of the answer"


def test_second_decision_press_cannot_change_the_answer(run, tg, poller):
    did = decision(run)
    ask = tg.message("Approver:")
    press(tg, poller, ask, "Hold the truck")
    assert "Kuch nahi badla" in press(tg, poller, ask, "Approve under target")
    assert answer_of(did)[0] == "Hold the truck"


def test_approver_is_on_the_phone_only_with_use_my_phone(run, tg):
    assert channels.is_live(run, "approver", "approver")
    store.set_kv(run, "fleet_live_roles", [])
    assert not channels.is_live(run, "approver", "approver")


def test_typed_commands_and_text_replies_still_work(run, tg, poller):
    offered(run, tg)
    tg.push(message={"chat": {"id": CHAT}, "text": "/driver D03 haan"})
    poller.poll_once(TOKEN)
    assert fleet.job_for(run, "V3")["driver"] == "D03"
    tg.push(message={"chat": {"id": CHAT}, "text": "/gate V3 in"})
    poller.poll_once(TOKEN)
    assert fleet.job_for(run, "V3")["gate"]["in"]["by"] == "phone"
    did = decision(run)
    tg.push(message={"chat": {"id": CHAT}, "text": "Hold the truck"})
    poller.poll_once(TOKEN)
    assert answer_of(did) == ("Hold the truck", "phone")


def test_a_photo_does_not_answer_a_decision(run, tg, poller):
    did = decision(run)
    tg.push(message={"chat": {"id": CHAT}, "photo": [{"file_id": "x"}]})
    poller.poll_once(TOKEN)
    assert answer_of(did)[0] is None


def test_no_screen_button_without_a_public_address(run, tg):
    _, offer = offered(run, tg)
    assert "Open my screen" not in labels(offer)


def test_screen_button_points_at_the_right_page_with_the_key(run, tg, poller, monkeypatch):
    monkeypatch.setenv("TREX_PUBLIC_URL", "https://team.example.com/")
    monkeypatch.setenv("TREX_PUBLIC_KEY", "k 1")
    _, offer = offered(run, tg)
    assert labels(offer) == ["Accept", "Reject", "Open my screen"]
    url = offer["reply_markup"]["inline_keyboard"][-1][0]["url"]
    assert url == "https://team.example.com/driver?id=D03&key=k+1"
    press(tg, poller, offer, "Accept")
    assert labels(tg.edits()[-1]) == ["Open my screen"], "The screen button outlives the answer"
    note = tg.message("Dispatcher:")
    assert note["reply_markup"]["inline_keyboard"][-1][0]["url"] == "https://team.example.com/gate?key=k+1"
    assert channels.screen_url(run, "retailer_desk", "R-120") == "https://team.example.com/call?shop=R-120&key=k+1"


def test_screen_button_is_left_out_for_an_address_telegram_would_refuse(run, monkeypatch, tg):
    for bad in ("http://localhost:8000", "http://127.0.0.1:8000", "team.example.com"):
        monkeypatch.setenv("TREX_PUBLIC_URL", bad)
        assert channels.screen_url(run, "dispatcher", "dispatch-desk") is None


def test_a_refused_screen_button_does_not_cost_the_message_or_its_action_buttons(run, tg, monkeypatch):
    monkeypatch.setenv("TREX_PUBLIC_URL", "https://team.example.com")
    tg.refuse = [httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request: wrong url"})]
    result = channels.send(run, "dispatcher", "dispatch-desk", "hello", f"{run}:k-url", None, channels.gate_buttons(run, {"truck": "V3", "version": 1, "driver": "D03"}))
    assert result["status"] == "sent"
    assert labels(tg.sent()[0]) == ["Gate in", "Gate out", "Open my screen"]
    assert labels(tg.sent()[1]) == ["Gate in", "Gate out"]


@pytest.mark.parametrize("failure", [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), httpx.Response(500, text="<html>boom"), httpx.Response(403, json={"ok": False, "error_code": 403, "description": f"blocked at bot{TOKEN}"})])
def test_a_failed_send_is_logged_and_never_raised(run, tg, failure):
    tg.refuse = [failure]
    result = channels.send(run, "dispatcher", "dispatch-desk", "hello", f"{run}:k-fail")
    assert result["status"] == "failed"
    assert len(tg.sent()) == 1, "Not resent, as it may already have arrived"
    events = store.rows("select data from events where run=? and kind='telegram_send_failed'", (run,))
    assert len(events) == 1 and TOKEN not in events[0]["data"]
    assert store.rows("select status from outbox where key=?", (f"{run}:k-fail",))[0]["status"] == "failed"
    assert channels.send(run, "dispatcher", "dispatch-desk", "hello", f"{run}:k-fail")["status"] == "already_sent"


def test_a_failed_send_does_not_stop_a_driver_taking_the_job(run, tg, poller):
    _, offer = offered(run, tg)
    tg.refuse = [httpx.Response(200, json={"ok": True, "result": True}), httpx.ConnectError("down"), httpx.ConnectError("down"), httpx.ConnectError("down")]
    press(tg, poller, offer, "Accept")
    assert fleet.job_for(run, "V3")["driver"] == "D03"


def test_one_bad_update_does_not_end_the_poller(run, tg, poller, monkeypatch):
    offered(run, tg)
    tg.push(message={"chat": {"id": CHAT}, "text": "/shop R-NOPE 5 peti paneer chahiye"})
    tg.push(message={"chat": {"id": CHAT}, "text": "/driver D03 haan"})
    poller.poll_once(TOKEN)
    assert fleet.job_for(run, "V3")["driver"] == "D03"


def test_a_refused_poll_waits_instead_of_spinning(tg, poller, monkeypatch):
    waits = []
    monkeypatch.setattr(channels.time, "sleep", waits.append)
    monkeypatch.setattr(channels.httpx, "get", lambda *a, **k: httpx.Response(409, json={"ok": False, "error_code": 409, "description": "Conflict"}))
    poller.poll_once(TOKEN)
    assert waits == [5]


def test_a_garbled_button_is_answered_and_changes_nothing(run, tg, poller):
    for data in ("nonsense", f"o|{run}|V3|x|1|D03|y", f"a|{run}|nope|0", f"a|{run}"):
        tg.push(callback_query={"id": "z", "data": data, "message": {"message_id": 1, "chat": {"id": CHAT}, "text": "t"}})
        poller.poll_once(TOKEN)
    assert len(tg.answers()) == 4


def test_the_token_never_reaches_saved_records(run, tg, poller):
    tools, retailer, ask = on_the_road(run, tg, poller)
    press(tg, poller, ask, "Delivered")
    for table in ("events", "outbox", "inbox", "kv"):
        assert not [r for r in store.rows(f"select * from {table}") if TOKEN in json.dumps(r, default=str)], table


def test_a_stranger_cannot_take_over_the_alerts(run):
    from trex import channels, store

    store.set_kv(channels.GLOBAL, "telegram_chat", 111)
    poller = channels.TelegramPoller.__new__(channels.TelegramPoller)
    poller.latest_run = lambda: None
    poller.handle({"update_id": 1, "message": {"chat": {"id": 222}, "text": "/start"}}, "token")
    assert store.get_kv(channels.GLOBAL, "telegram_chat") == 111
