"""Dev: проверить, какие «невидимые» тексты Telegram принимает (временный)."""
import json
import os
import urllib.request

TOKEN = os.environ["BOT_TOKEN"]
CHAT = int(os.environ["ADMIN_CHAT_ID"])


def send(txt: str):
    data = json.dumps({"chat_id": CHAT, "text": txt}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        r = json.load(urllib.request.urlopen(req))
        return r.get("ok"), r.get("result", {}).get("message_id")
    except urllib.error.HTTPError as e:
        return False, e.read().decode()[:150]


for t in ["\u00a0", " ", "\u200b", "\u200c", "·", "."]:
    ok, res = send(t)
    print(repr(t), "->", ok, res)
    if ok:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/deleteMessage",
            data=json.dumps({"chat_id": CHAT, "message_id": res}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)