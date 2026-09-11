"""端到端验证：SSE 流式 + RAG + LLM 商品描述。"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000/api/v1"


def test_sse_stream(cid: int, msg: str, label: str):
    """测试 SSE 流式端点：逐事件读取，验证 intent/token/done。"""
    print(f"\n[SSE 流式] {label}")
    print(f"  cid={cid} msg={msg}")
    body = json.dumps({"customer_id": cid, "message": msg}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/customer-service/chat/stream", data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=90)
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        return
    content_type = r.headers.get("Content-Type", "")
    print(f"  Content-Type: {content_type}")
    buf = b""
    full_reply = ""
    intent = None
    token_count = 0
    done = None
    while True:
        chunk = r.read(4096)
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            ev_block, buf = buf.split(b"\n\n", 1)
            ev = ev_block.decode("utf-8", "replace")
            event = "message"
            data = ""
            for line in ev.split("\n"):
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data += line[5:].strip()
            if not data:
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            if event == "intent":
                intent = payload.get("intent")
                print(f"  [event=intent] intent={intent}")
            elif event == "token":
                token_count += 1
                full_reply += payload.get("content", "")
            elif event == "done":
                done = payload
                print(f"  [event=done] source={payload.get('source')} session_id={payload.get('session_id')}")
    print(f"  token 段数: {token_count}")
    print(f"  intent: {intent}")
    print(f"  完整回复: {full_reply}")
    return intent, full_reply, done


def test_rag_kb(cid: int, msg: str, label: str):
    """测试 RAG：问知识库内的具体问题，看回复是否引用了 KB 内容。"""
    print(f"\n[RAG 知识库] {label}")
    print(f"  cid={cid} msg={msg}")
    body = json.dumps({"customer_id": cid, "message": msg}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/customer-service/chat", data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=90)
        data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}")
        return
    print(f"  intent: {data.get('intent')}")
    print(f"  source: {data.get('source')}")
    print(f"  reply : {data.get('reply')}")
    return data


def test_product_description(pid: int, lang: str):
    """测试 LLM 商品描述生成。"""
    print(f"\n[LLM 商品描述] pid={pid} lang={lang}")
    req = urllib.request.Request(
        f"{BASE}/recommendation/products/{pid}/description?language={lang}"
    )
    try:
        r = urllib.request.urlopen(req, timeout=90)
        data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}")
        return
    print(f"  商品: {data.get('name')} | {data.get('category')} | ${data.get('price_usd')}")
    print(f"  描述: {data.get('description')}")
    return data


def test_recommendations_with_desc(cid: int, lang: str):
    """测试推荐 + LLM 推荐理由。"""
    print(f"\n[推荐 + LLM 推荐理由] cid={cid} lang={lang}")
    req = urllib.request.Request(
        f"{BASE}/recommendation/users/{cid}/recommendations-with-desc?language={lang}&top_k=3"
    )
    try:
        r = urllib.request.urlopen(req, timeout=120)
        data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}")
        return
    for it in data:
        print(f"  [{it['product_id']}] {it['name']} | score={it['score']}")
        print(f"     理由: {it.get('description')}")
    return data


if __name__ == "__main__":
    print("=" * 60)
    print("端到端验证：SSE 流式 + RAG + LLM 商品描述")
    print("=" * 60)

    # 1. SSE 流式 - 日文客户问物流
    test_sse_stream(2, "私の荷物がまだ届きません。配送状況を教えてください。", "日文物流 SSE")

    # 2. SSE 流式 - 德文客户问关税（开放性，可触发 RAG）
    test_sse_stream(25, "Wie werden die Zollgebühren berechnet und wann muss ich sie zahlen?", "德文关税 SSE")

    # 3. RAG 知识库 - 问退款政策（KB 有相关条目）
    test_rag_kb(1, "What's your refund policy and how long does it take?", "退款政策 RAG")

    # 4. RAG 知识库 - 问运费承运商（KB 有具体承运商信息）
    test_rag_kb(1, "Which carriers do you use for international shipping?", "承运商 RAG")

    # 5. LLM 商品描述 - 日语
    test_product_description(1, "ja")

    # 6. LLM 商品描述 - 阿拉伯语
    test_product_description(16, "ar")

    # 7. 推荐 + LLM 推荐理由 - 英文客户
    test_recommendations_with_desc(1, "en")

    print("\n" + "=" * 60)
    print("全部测试完成")
    print("=" * 60)
