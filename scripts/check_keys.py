"""Check each key with the cheapest possible call. Never prints a key."""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path.home() / ".trex.env")


def report(name: str, ok: bool, detail: str = "") -> None:
    print(f"{name}: {'OK' if ok else 'FAILED'} {detail}".strip())


def openai_key() -> None:
    key = os.environ.get("CHATGPT_API_KEY")
    if not key:
        return report("OpenAI", False, "not set")
    import openai

    model = os.environ.get("TREX_MODEL", "gpt-6.1-sol")
    tool = {"type": "function", "name": "ping", "description": "Ping", "strict": True, "parameters": {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"], "additionalProperties": False}}
    try:
        r = openai.OpenAI(api_key=key).responses.create(model=model, reasoning={"effort": "low"}, max_output_tokens=400, tools=[tool], input="Call ping with the word ok", store=False, include=["reasoning.encrypted_content"])
        kinds = [i.type for i in r.output]
        report("OpenAI", True, f"model={model} output={kinds}")
    except openai.APIStatusError as err:
        report("OpenAI", False, f"model={model} status {err.status_code}: {str(err.message)[:140]}")


def crustdata_key() -> None:
    key = os.environ.get("CRUSTDATA_API_KEY")
    if not key:
        return report("Crustdata", False, "not set")
    r = httpx.post("https://api.crustdata.com/company/identify", headers={"authorization": f"Bearer {key}", "x-api-version": "2025-11-01", "content-type": "application/json"}, json={"domains": ["crustdata.com"]}, timeout=30)
    report("Crustdata", r.status_code == 200, f"status {r.status_code}")


def telegram_key() -> None:
    key = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not key:
        return report("Telegram", False, "not set")
    r = httpx.get(f"https://api.telegram.org/bot{key}/getMe", timeout=30)
    report("Telegram", r.status_code == 200, f"bot @{r.json().get('result', {}).get('username')}" if r.status_code == 200 else f"status {r.status_code}")


def twilio_key() -> None:
    sid, token = os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")
    if not (sid and token):
        return report("Twilio", False, "not set")
    r = httpx.get(f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json", auth=(sid, token), timeout=30)
    report("Twilio", r.status_code == 200, f"status {r.status_code}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for check in (openai_key, crustdata_key, telegram_key, twilio_key):
        try:
            check()
        except Exception as err:
            print(check.__name__, "FAILED", type(err).__name__)
