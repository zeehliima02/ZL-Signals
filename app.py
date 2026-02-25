import os
import json
import re
import time
import hashlib
from pathlib import Path

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

STATE_FILE = Path("state.json")
DUP_WINDOW_SECONDS = 5


def default_state():
    return {
        "last_global_id": 0,
        "open_trade_global_id": None,
        "open_trade_status": "closed",
        "last_hash": "",
        "last_hash_ts": 0
    }


def load_state():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            s = default_state()
            s.update(data if isinstance(data, dict) else {})
            return s
        except Exception:
            pass
    s = default_state()
    save_state(s)
    return s


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram_message(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN ou CHAT_ID não configurados.")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def classify_message(text: str) -> str:
    if "✅ Entrada confirmada" in text:
        return "entry"
    if "TP1 ATINGIDO" in text:
        return "tp"
    if "STOP LOSS ATINGIDO" in text:
        return "sl"
    return "other"


def replace_id_in_text(text: str, new_id: int) -> str:
    pattern = r"🆔\s*ID:\s*\d+"
    replacement = f"🆔 ID: {new_id}"
    if re.search(pattern, text):
        return re.sub(pattern, replacement, text, count=1)
    return text


def is_duplicate_and_update(state: dict, text: str) -> bool:
    now = int(time.time())
    msg_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    is_dup = (
        state.get("last_hash") == msg_hash and
        (now - int(state.get("last_hash_ts", 0))) <= DUP_WINDOW_SECONDS
    )

    state["last_hash"] = msg_hash
    state["last_hash_ts"] = now
    return is_dup


def apply_persistent_id(text: str) -> tuple[str, dict]:
    state = load_state()

    # Anti-duplicação (não incrementa ID se repetir a mesma msg em poucos segundos)
    if is_duplicate_and_update(state, text):
        save_state(state)
        return text, {"duplicate": True, "kind": "duplicate", "official_id": None}

    kind = classify_message(text)
    official_id = None

    if kind == "entry":
        official_id = int(state.get("last_global_id", 0)) + 1
        state["last_global_id"] = official_id
        state["open_trade_global_id"] = official_id
        state["open_trade_status"] = "open"
        text = replace_id_in_text(text, official_id)

    elif kind in ("tp", "sl"):
        open_id = state.get("open_trade_global_id")
        if open_id is not None:
            official_id = int(open_id)
        else:
            # fallback: usa último ID conhecido (caso chegue saída sem estado aberto)
            official_id = int(state.get("last_global_id", 0))

        text = replace_id_in_text(text, official_id)
        state["open_trade_global_id"] = None
        state["open_trade_status"] = "closed"

    # "other" passa sem mexer no ID

    save_state(state)
    return text, {"duplicate": False, "kind": kind, "official_id": official_id}


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


# Teste fácil pelo navegador (GET) com segredo
# Ex: /test?secret=SEU_SEGREDO&text=Ola
@app.get("/test")
def test_send():
    secret = request.args.get("secret", "")
    text = request.args.get("text", "✅ Teste do webhook")

    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "invalid secret"}), 403

    # /test envia direto (sem controle de ID)
    send_telegram_message(text)
    return jsonify({"ok": True})


# Endpoint que o TradingView vai chamar (POST)
@app.post("/tv")
def tradingview_webhook():
    raw = request.get_data(as_text=True).strip()
    if not raw:
        return jsonify({"ok": False, "error": "empty body"}), 400

    # aceita JSON ou texto
    secret = ""
    text = raw
    try:
        data = json.loads(raw)
        secret = str(data.get("secret", ""))
        text = str(data.get("text", data.get("message", raw)))
    except json.JSONDecodeError:
        pass

    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "invalid secret"}), 403

    final_text, info = apply_persistent_id(text)

    if info.get("duplicate"):
        return jsonify({"ok": True, "duplicate_ignored": True})

    send_telegram_message(final_text)
    return jsonify({"ok": True, "kind": info.get("kind"), "official_id": info.get("official_id")})
