"""Test order system. It fails on purpose, the way real distributor systems do."""
import json
import time

from . import store


class ServerError(Exception):
    pass


class Timeout(Exception):
    pass


class Conflict(Exception):
    pass


FAULTS = ("500_after_write", "timeout_after_write", "500_before_write", "409_stale_stock", "malformed")


def inject(run: str, fault: str | None, times: int = 1) -> None:
    if fault is not None and fault not in FAULTS:
        raise ValueError(fault)
    store.set_kv(run, "order_fault", {"fault": fault, "times": times})


def _take_fault(run: str) -> str | None:
    f = store.get_kv(run, "order_fault", {"fault": None, "times": 0})
    if f["fault"] and f["times"] > 0:
        store.set_kv(run, "order_fault", {"fault": f["fault"], "times": f["times"] - 1})
        return f["fault"]
    return None


def write(run: str, key: str, payload: dict) -> dict:
    """Saves an order. Like many older systems, it gives no safe way to tell a failed write from a saved one."""
    fault = _take_fault(run)
    if fault == "500_before_write":
        raise ServerError("500 Internal Server Error")
    if fault == "409_stale_stock":
        raise Conflict("409 stock changed")
    store.execute("insert or ignore into test_orders(key, run, payload, ts) values(?,?,?,?)", (key, run, json.dumps(payload), time.time()))
    if fault == "500_after_write":
        raise ServerError("500 Internal Server Error")
    if fault == "timeout_after_write":
        raise Timeout("request timed out")
    if fault == "malformed":
        return {"raw": "<<<html>bad gateway"}
    return {"status": "saved", "key": key}


def read(run: str, key: str) -> dict | None:
    found = store.rows("select payload from test_orders where key=? and run=?", (key, run))
    return json.loads(found[0]["payload"]) if found else None


def count(run: str) -> int:
    return store.rows("select count(*) c from test_orders where run=?", (run,))[0]["c"]


def write_safely(run: str, key: str, payload: dict, retries: int = 2) -> dict:
    """Write once. After any failure, read back first, and retry only if the order is truly absent."""
    if read(run, key) is not None:
        store.log(run, "order_already_saved", key=key)
        return {"status": "already_saved", "key": key}
    last = None
    for attempt in range(retries + 1):
        try:
            result = write(run, key, payload)
            if result.get("status") != "saved":
                raise ServerError(f"unreadable reply: {str(result)[:60]}")
            return {"status": "saved", "key": key, "attempts": attempt + 1}
        except Conflict as err:
            store.log(run, "order_conflict", key=key, error=str(err))
            return {"status": "conflict", "key": key, "error": str(err)}
        except (ServerError, Timeout) as err:
            last = str(err)
            store.log(run, "order_error", key=key, error=last, attempt=attempt + 1)
            if read(run, key) is not None:
                store.log(run, "order_read_back_found", key=key, note="Saved despite the error, so no duplicate was created")
                return {"status": "saved_after_error", "key": key, "error": last}
    return {"status": "failed", "key": key, "error": last}
