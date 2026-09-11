"""UI ↔ 后端 API 契约测试。

前端 JS 读取响应中的固定字段名（如 i.total_sales、s.risk_score），
本测试确保后端实际响应结构与前端期望一致，防止 UI 渲染 undefined。

需要服务运行于 http://127.0.0.1:8000，未启动时自动跳过。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import unittest

SERVER = "http://127.0.0.1:8000"
API = SERVER + "/api/v1"


def get_json(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(url: str, body: dict, timeout: int = 120):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def server_alive() -> bool:
    try:
        with urllib.request.urlopen(SERVER + "/health", timeout=3):
            return True
    except Exception:
        return False


class APIContractTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not server_alive():
            raise unittest.SkipTest("服务未运行（http://127.0.0.1:8000），跳过契约测试")


class TestHomePage(APIContractTestBase):
    def test_index_served_with_ui_optimizations(self):
        """FastAPI 静态挂载返回的是含优化标记的最新 HTML。"""
        with urllib.request.urlopen(SERVER + "/", timeout=10) as r:
            html = r.read().decode("utf-8")
        self.assertEqual(r.status, 200)
        for marker in ("fadeIn", "skel-bar", "es-icon", "kpi-red", "nth-child(even)"):
            self.assertIn(marker, html, f"服务的 HTML 缺少优化标记: {marker}")


class TestMarketContract(APIContractTestBase):
    def test_trending_fields(self):
        """热门品类条形图依赖: category/region/score/total_sales。"""
        items = get_json(f"{API}/market/trending?limit=3")
        self.assertIsInstance(items, list)
        self.assertTrue(items, "trending 返回空列表")
        for i in items:
            for field in ("category", "region", "score", "total_sales"):
                self.assertIn(field, i, f"trending 缺字段 {field}")

    def test_select_recommendations_fields(self):
        """选品建议卡依赖: name/score/category/reasons。"""
        items = get_json(f"{API}/market/select-recommendations?limit=3")
        self.assertTrue(items, "select-recommendations 返回空列表")
        for i in items:
            for field in ("name", "score", "category", "reasons"):
                self.assertIn(field, i, f"select-recommendations 缺字段 {field}")
            self.assertIsInstance(i["reasons"], list)


class TestSupplyChainContract(APIContractTestBase):
    def test_inventory_alerts_fields(self):
        """库存预警表依赖: product_name/warehouse_code/stock/safety_stock/gap/suggestion。"""
        items = get_json(f"{API}/supply-chain/inventory/alerts")
        for i in items[:5]:
            for field in ("product_name", "warehouse_code", "stock",
                          "safety_stock", "gap", "suggestion"):
                self.assertIn(field, i, f"inventory alerts 缺字段 {field}")

    def test_shipment_risk_fields(self):
        """物流风险表依赖: shipment_id/route/carrier/status/risk_score/level。"""
        items = get_json(f"{API}/supply-chain/shipments/risk")
        self.assertTrue(items, "shipments/risk 返回空列表")
        for s in items[:5]:
            for field in ("shipment_id", "route", "carrier", "status",
                          "risk_score", "level"):
                self.assertIn(field, s, f"shipment risk 缺字段 {field}")
        # 等级取值必须与前端 riskBadge 映射匹配
        valid_levels = {"高风险", "中风险", "低风险"}
        for s in items[:20]:
            self.assertIn(s["level"], valid_levels, f"未知风险等级 {s['level']}")

    def test_shipment_status_values_match_ui_map(self):
        """运单状态取值必须与前端 SHIP_STATUS 映射匹配。"""
        items = get_json(f"{API}/supply-chain/shipments/risk")
        valid_statuses = {"delivered", "in_transit", "customs", "delayed", "pending"}
        for s in items[:50]:
            self.assertIn(s["status"], valid_statuses, f"未知状态 {s['status']}")

    def test_eta_fields(self):
        """ETA 预测依赖: shipment_id/route/carrier/predicted_days/confidence。"""
        d = get_json(f"{API}/supply-chain/logistics/eta/1")
        for field in ("shipment_id", "route", "carrier", "predicted_days", "confidence"):
            self.assertIn(field, d, f"eta 缺字段 {field}")
        # 前端 confidence 映射支持 high/mid/medium/low
        self.assertIn(str(d["confidence"]).lower(), {"high", "mid", "medium", "low"})

    def test_shipment_id_route(self):
        """UI 输入运单 ID 预测时效，不存在的 ID 应返回 404。"""
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            get_json(f"{API}/supply-chain/logistics/eta/99999999")
        self.assertEqual(ctx.exception.code, 404)


class TestCustomerServiceContract(APIContractTestBase):
    def test_customers_fields(self):
        """客户下拉框依赖: id/name/country/language。"""
        items = get_json(f"{API}/customer-service/customers")
        self.assertTrue(items, "customers 返回空列表")
        for c in items[:5]:
            for field in ("id", "name", "country", "language"):
                self.assertIn(field, c, f"customers 缺字段 {field}")

    def test_customer_sessions_messages_shape(self):
        """会话历史消息依赖 role/content 字段（前端按 role 分流渲染）。"""
        customers = get_json(f"{API}/customer-service/customers")
        cid = customers[0]["id"]
        sessions = get_json(f"{API}/customer-service/customers/{cid}/sessions")
        self.assertIsInstance(sessions, list)
        for s in sessions[:1]:
            self.assertIn("messages", s)
            for m in s["messages"]:
                self.assertIn("role", m)
                self.assertIn("content", m)
                self.assertIn(m["role"], ("user", "bot"))

    def test_chat_returns_required_fields(self):
        """非流式对话依赖: session_id/intent/reply/language/timestamp/source。"""
        customers = get_json(f"{API}/customer-service/customers")
        cid = customers[0]["id"]
        d = post_json(f"{API}/customer-service/chat",
                      {"customer_id": cid, "message": "Where is my shipping?"})
        for field in ("session_id", "intent", "reply", "language", "timestamp", "source"):
            self.assertIn(field, d, f"chat 缺字段 {field}")
        # 意图必须在前端 intentClass 映射范围内
        self.assertIn(d["intent"], {"物流", "退款", "库存", "关税", "其他"})


class TestSSEStreamContract(APIContractTestBase):
    def test_stream_event_sequence(self):
        """SSE 流必须按 sources → intent → token* → done 顺序输出（前端按此解析）。"""
        customers = get_json(f"{API}/customer-service/customers")
        cid = customers[0]["id"]
        body = json.dumps({"customer_id": cid, "message": "Where is my shipping?"}).encode("utf-8")
        req = urllib.request.Request(
            f"{API}/customer-service/chat/stream", data=body,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        events: list[str] = []
        buf = b""
        with urllib.request.urlopen(req, timeout=120) as r:
            self.assertEqual(r.headers.get("Content-Type", "").startswith("text/event-stream"), True)
            while True:
                chunk = r.read(2048)
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    block, buf = buf.split(b"\n\n", 1)
                    for line in block.decode("utf-8", "replace").split("\n"):
                        if line.startswith("event:"):
                            events.append(line[6:].strip())
        self.assertIn("sources", events, "缺少 sources 事件")
        self.assertIn("intent", events, "缺少 intent 事件")
        self.assertIn("done", events, "缺少 done 事件")
        self.assertGreater(events.count("token"), 0, "缺少 token 事件")
        # 顺序约束：sources 最先，done 最后
        self.assertEqual(events[0], "sources")
        self.assertEqual(events[-1], "done")


class TestRecommendationContract(APIContractTestBase):
    def test_recommendations_with_desc_fields(self):
        """推荐卡片依赖: name/score/category/price_usd/description。"""
        customers = get_json(f"{API}/customer-service/customers")
        cid = customers[0]["id"]
        items = get_json(f"{API}/recommendation/users/{cid}/recommendations-with-desc"
                         f"?top_k=3&language=en", timeout=120)
        self.assertTrue(items, "recommendations-with-desc 返回空列表")
        for i in items:
            for field in ("name", "score", "category", "price_usd", "description"):
                self.assertIn(field, i, f"recommendation 缺字段 {field}")

    def test_product_description_fields(self):
        """单品描述生成依赖: name/category/price_usd/description。"""
        d = get_json(f"{API}/recommendation/products/1/description?language=en", timeout=120)
        for field in ("name", "category", "price_usd", "description"):
            self.assertIn(field, d, f"product description 缺字段 {field}")
        self.assertTrue(d["description"], "description 为空")


if __name__ == "__main__":
    unittest.main()
