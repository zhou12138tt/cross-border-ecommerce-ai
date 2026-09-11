"""完整版端到端验证：KB 管理 + RAG sources + 四大模块 LLM 报告。"""
import json
import sys
import urllib.request
import urllib.error
import urllib.parse

BASE = "http://127.0.0.1:8000/api/v1"


def call(method, path, body=None, timeout=90, query=None):
    if query:
        qs = urllib.parse.urlencode(query)
        url = f"{BASE}{path}?{qs}"
    else:
        url = f"{BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method=method,
    )
    r = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(r.read().decode("utf-8"))


def test_kb_crud():
    print("\n" + "=" * 60)
    print("[1] 知识库 CRUD + 重建索引")
    print("=" * 60)

    # 列表
    items = call("GET", "/knowledge-base")
    print(f"  列表：{len(items)} 条 FAQ")

    # 检索测试（不经过 LLM）
    hits = call("GET", "/knowledge-base/retrieve/test",
                query={"q": "退款政策几天到账", "top_k": 2})
    print(f"  检索测试：{len(hits)} 条命中")
    for h in hits:
        print(f"    - [{h['id']}] {h['category']} 相似度={h['score']} Q={h['question'][:30]}")

    # 新建
    new_item = call("POST", "/knowledge-base", {
        "kb_id": "kb_test_001",
        "category": "测试",
        "question": "测试 FAQ 端到端",
        "answer": "这是一条测试 FAQ。",
    })
    print(f"  新建：id={new_item['id']} kb_id={new_item['kb_id']}")

    # 更新
    upd = call("PUT", "/knowledge-base/kb_test_001", {"answer": "更新后的答案"})
    print(f"  更新：answer={upd['answer']}")

    # 删除
    call("DELETE", "/knowledge-base/kb_test_001")
    print(f"  删除：kb_test_001")

    # 重建索引
    r = call("POST", "/knowledge-base/reload")
    print(f"  重建索引：{r}")


def test_sse_sources(cid, msg, label):
    print("\n" + "=" * 60)
    print(f"[2] SSE 流式 + RAG sources —— {label}")
    print("=" * 60)
    body = json.dumps({"customer_id": cid, "message": msg}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/customer-service/chat/stream", data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    r = urllib.request.urlopen(req, timeout=120)
    buf = b""
    full_reply = ""
    intent = None
    sources = []
    token_count = 0
    while True:
        chunk = r.read(2048)
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
            if event == "sources":
                sources = payload
                print(f"  [sources] 检索到 {len(sources)} 条 FAQ 引用")
                for s in sources:
                    print(f"    - [{s['id']}] {s['category']} 相似度={s['score']}")
            elif event == "intent":
                intent = payload.get("intent")
                print(f"  [intent] {intent}")
            elif event == "token":
                token_count += 1
                full_reply += payload.get("content", "")
            elif event == "done":
                print(f"  [done] source={payload.get('source')} session={payload.get('session_id')}")
                if payload.get("sources"):
                    sources = payload["sources"]
    print(f"  token 数: {token_count}")
    print(f"  完整回复: {full_reply}")
    print(f"  来源引用数: {len(sources)}")


def test_market_insight():
    print("\n" + "=" * 60)
    print("[3] 市场分析 LLM 选品洞察报告")
    print("=" * 60)
    r = call("GET", "/market/insight-report", query={"top_n": 5})
    print(f"  source: {r['source']}")
    print(f"  候选数: {len(r['top_picks'])}")
    for p in r["top_picks"]:
        print(f"    - [{p['product_id']}] {p['name']} 评分={p['score']}")
    print(f"  报告:\n{r['report']}")


def test_supply_risk():
    print("\n" + "=" * 60)
    print("[4] 供应链 LLM 风险预警报告")
    print("=" * 60)
    r = call("GET", "/supply-chain/risk-report")
    print(f"  source: {r['source']}")
    print(f"  库存预警: {len(r['inventory_alerts'])} 条")
    print(f"  物流风险: {len(r['shipment_risks'])} 条")
    print(f"  报告:\n{r['report']}")


def test_product_desc():
    print("\n" + "=" * 60)
    print("[5] 推荐模块 LLM 商品描述（日文）")
    print("=" * 60)
    r = call("GET", "/recommendation/products/1/description", query={"language": "ja"})
    print(f"  商品: {r['name']} | {r['category']} | ${r['price_usd']}")
    print(f"  描述: {r['description']}")


if __name__ == "__main__":
    test_kb_crud()
    test_sse_sources(1, "What is your refund policy and how long does it take?", "退款政策 RAG sources")
    test_market_insight()
    test_supply_risk()
    test_product_desc()
    print("\n" + "=" * 60)
    print("完整版端到端验证全部完成")
    print("=" * 60)
