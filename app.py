import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

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

    send_telegram_message(text)
    return jsonify({"ok": True})
