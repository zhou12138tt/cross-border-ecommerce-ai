"""快速验证 DeepSeek 客服端点。"""
import json
import sys
from pathlib import Path
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1/customer-service"

cases = [
    (1, "I want a refund for order #12345", "en"),
    (2, "私の荷物がまだ届きません、配送状況は？", "ja"),
    (11, "你们有礼品卡吗？怎么用？", "ar"),
    (25, "Wie lange dauert die Lieferung nach München?", "de"),
]

for cid, msg, lang in cases:
    body = json.dumps({"customer_id": cid, "message": msg}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/chat", data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        print(f"\n[cid={cid} lang={lang}] {msg}")
        print(f"  intent : {data['intent']}")
        print(f"  source : {data['source']}")
        print(f"  reply  : {data['reply']}")
    except Exception as e:
        print(f"[cid={cid}] ERROR: {e}")
