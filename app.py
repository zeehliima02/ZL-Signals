import os
import json
import re
import time
import hashlib
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# NOVO: integração com planilha (Apps Script)
SHEET_API_URL = os.environ.get("SHEET_API_URL", "")
SHEET_API_TOKEN = os.environ.get("SHEET_API_TOKEN", "")


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


# =========================
# PLANILHA (Google Sheets API via Apps Script)
# =========================
def sheet_get_state(symbol: str) -> dict:
    if not SHEET_API_URL or not SHEET_API_TOKEN:
        raise RuntimeError("SHEET_API_URL ou SHEET_API_TOKEN não configurados.")

    r = requests.get(
        SHEET_API_URL,
        params={
            "action": "get",
            "token": SHEET_API_TOKEN,
            "symbol": symbol
        },
        timeout=10
    )
    r.raise_for_status()
    data = r.json()

    if not data.get("ok"):
        raise RuntimeError(f"Erro planilha GET: {data}")

    return data["state"]


def sheet_save_state(symbol: str, state: dict):
    if not SHEET_API_URL or not SHEET_API_TOKEN:
        raise RuntimeError("SHEET_API_URL ou SHEET_API_TOKEN não configurados.")

    payload = {
        "action": "save",
        "token": SHEET_API_TOKEN,
        "symbol": symbol,
        "last_global_id": state.get("last_global_id", 0),
        "open_trade_global_id": state.get("open_trade_global_id"),
        "open_trade_status": state.get("open_trade_status", "closed"),
        "last_hash": state.get("last_hash", ""),
        "last_hash_ts": state.get("last_hash_ts", 0),
    }

    r = requests.post(SHEET_API_URL, json=payload, timeout=10)
    r.raise_for_status()
    data = r.json()

    if not data.get("ok"):
        raise RuntimeError(f"Erro planilha SAVE: {data}")


def classify_message(text: str) -> str:
    if "✅ Entrada confirmada" in text:
        return "entry"
    if "TP2 ATINGIDO" in text:
        return "tp2"
    if "TP1 ATINGIDO" in text:
        return "tp1"
    if "STOP LOSS ATINGIDO" in text:
        return "sl"
    return "other"


def extract_symbol(text: str) -> str:
    # hoje teu layout usa XAUUSD; já deixei preparado pro futuro
    if "EURUSD" in text:
        return "EURUSD"
    if "XAUUSD" in text:
        return "XAUUSD"
    return "XAUUSD"  # padrão atual


def replace_id_in_text(text: str, new_id: int) -> str:
    # Troca a primeira ocorrência da linha de ID
    return re.sub(r"🆔\s*ID:\s*\d+", f"🆔 ID: {new_id}", text, count=1)


def apply_persistent_id_with_sheet(text: str):
    symbol = extract_symbol(text)
    kind = classify_message(text)
    state = sheet_get_state(symbol)

    # Anti-duplicação simples (5s)
    now_ts = int(time.time())
    msg_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    last_hash = str(state.get("last_hash", ""))
    last_hash_ts = int(state.get("last_hash_ts", 0) or 0)

    if last_hash == msg_hash and (now_ts - last_hash_ts) <= 5:
        return text, {"duplicate": True, "kind": "duplicate", "official_id": None, "symbol": symbol}

    official_id = None

    if kind == "entry":
        official_id = int(state.get("last_global_id", 0) or 0) + 1
        state["last_global_id"] = official_id
        state["open_trade_global_id"] = official_id
        state["open_trade_status"] = "open"
        text = replace_id_in_text(text, official_id)

    elif kind == "tp1":
        open_id = state.get("open_trade_global_id")
        if state.get("open_trade_status") != "open" or open_id in (None, "", "null"):
            return text, {"duplicate": true, "kind": "ghost_ignored", "official_id": None, "symbol": symbol}
            
        official_id = int(open_id)
        text = replace_id_in_text(text, official_id)    
            
    elif kind in ("tp2", "sl"):
        open_id = state.get("open_trade_global_id")
        if state.get("open_trade_status") != "open" or open_id in (None, "", "null"):
            return text, {"duplicate": true, "kind": "ghost_ignored", "official_id": None, "symbol": symbol}
        
        official_id = int(open_id)
        text = replace_id_in_text(text, official_id)
        state["open_trade_global_id"] = None
        state["open_trade_status"] = "closed"

    # Atualiza hash sempre (mesmo em "other")
    state["last_hash"] = msg_hash
    state["last_hash_ts"] = now_ts
    sheet_save_state(symbol, state)

    return text, {
        "duplicate": False,
        "kind": kind,
        "official_id": official_id,
        "symbol": symbol
    }


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

    final_text, info = apply_persistent_id_with_sheet(text)

    if info.get("duplicate"):
        return jsonify({"ok": True, "duplicate_ignored": True})

    send_telegram_message(final_text)
    return jsonify({
        "ok": True,
        "kind": info.get("kind"),
        "symbol": info.get("symbol"),
        "official_id": info.get("official_id")
    })
