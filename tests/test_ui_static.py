"""UI 优化静态内容测试。

验证 frontend/index.html 中 7 项 UI 优化均已落地：
    1. 页面切换淡入动画
    2. 骨架屏加载（shimmer 动画条）
    3. KPI 卡片升级（28px 大数字 + 左侧彩色条 + 背景图标）
    4. 表格 zebra 条纹 + hover 高亮
    5. 空状态图标（es-icon）
    6. 卡片 hover 阴影
    7. 侧边栏导航 active 左侧色条

同时覆盖关键 JS 逻辑的结构性断言（SSE 解析、意图映射、状态映射）。
"""
from __future__ import annotations

import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
BACKEND_CS = Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "customer_service.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class UIStaticTestBase(unittest.TestCase):
    """公共加载。"""

    @classmethod
    def setUpClass(cls):
        cls.html = read(FRONTEND)
        cls.css = cls.html.split("<style>")[1].split("</style>")[0]
        cls.js = cls.html.split("<script>")[1].split("</script>")[0]
        cls.body = cls.html.split("<body>")[1].split("</body>")[0]


class TestPageTransition(UIStaticTestBase):
    """优化 1：页面切换淡入动画。"""

    def test_fadein_keyframes_defined(self):
        self.assertIn("@keyframes fadeIn", self.css)

    def test_active_page_uses_fadein(self):
        self.assertIn(".page.active { animation: fadeIn", self.css)

    def test_fadein_animates_opacity_and_transform(self):
        self.assertIn("opacity: 0", self.css)
        self.assertIn("translateY(8px)", self.css)


class TestSkeletonLoading(UIStaticTestBase):
    """优化 2：骨架屏加载。"""

    def test_skeleton_css_defined(self):
        self.assertIn(".skeleton", self.css)
        self.assertIn(".skel-bar", self.css)

    def test_shimmer_animation_defined(self):
        self.assertIn("@keyframes shimmer", self.css)
        self.assertIn("background-size: 400% 100%", self.css)

    def test_skeleton_js_templates_defined(self):
        self.assertIn("const SKELETON_BARS", self.js)
        self.assertIn("const SKELETON_TABLE", self.js)

    def test_market_loaders_use_skeleton(self):
        # 市场分析两个卡片加载时先渲染骨架屏
        self.assertIn('$("trendingBody").innerHTML = SKELETON_BARS(', self.js)
        self.assertIn('$("pickBody").innerHTML = SKELETON_BARS(', self.js)

    def test_supply_loaders_use_skeleton(self):
        # 供应链两个表格加载时先渲染骨架屏
        self.assertIn('$("invBody").innerHTML = SKELETON_TABLE(', self.js)
        self.assertIn('$("shipBody").innerHTML = SKELETON_TABLE(', self.js)


class TestKPICards(UIStaticTestBase):
    """优化 3：KPI 卡片升级。"""

    def test_kpi_value_28px_bold(self):
        self.assertIn(".kpi .k-value", self.css)
        self.assertIn("font-size: 28px", self.css)
        self.assertIn("font-weight: 800", self.css)

    def test_kpi_color_bars_defined(self):
        self.assertIn(".kpi::before", self.css)
        self.assertIn(".kpi.kpi-red::before", self.css)
        self.assertIn(".kpi.kpi-amber::before", self.css)
        self.assertIn(".kpi.kpi-green::before", self.css)

    def test_render_kpis_emits_color_classes(self):
        # JS 按 预警>0/高风险>0 -> red，中风险>0 -> amber，否则 green
        self.assertIn('kpi ${inv > 0 ? "kpi-red" : "kpi-green"}', self.js)
        self.assertIn('kpi ${high > 0 ? "kpi-red" : "kpi-green"}', self.js)
        self.assertIn('kpi ${mid > 0 ? "kpi-amber" : "kpi-green"}', self.js)

    def test_kpi_background_icons(self):
        self.assertIn(".kpi .k-icon", self.css)
        for icon in ("📦", "🚨", "📋"):
            self.assertIn(icon, self.js)


class TestTableStyles(UIStaticTestBase):
    """优化 4：表格 zebra 条纹 + hover 高亮。"""

    def test_zebra_striping_on_even_rows(self):
        self.assertIn("table tbody tr:nth-child(even) td", self.css)

    def test_row_hover_highlight(self):
        self.assertIn("table tbody tr:hover td", self.css)
        self.assertIn("var(--primary-soft)", self.css)


class TestEmptyStates(UIStaticTestBase):
    """优化 5：空状态图标。"""

    def test_es_icon_css_defined(self):
        self.assertIn(".empty-state .es-icon", self.css)

    def test_empty_js_helper_defined(self):
        self.assertIn("const EMPTY = (icon, msg)", self.js)
        self.assertIn('<span class="es-icon">', self.js)

    def test_chat_empty_state_has_icon(self):
        # HTML 初始 1 处 + JS 模板（exitChat/loadHistory）3 处，均为带图标的空状态
        html_only = self.body.split("<script>")[0]  # body 含 script，需剥离
        self.assertEqual(html_only.count('<span class="es-icon">💬</span>'), 1)
        self.assertEqual(self.js.count('<span class="es-icon">💬</span>'), 3)

    def test_rec_empty_state_has_icon(self):
        self.assertIn('<span class="es-icon">🎯</span>选择客户后获取推荐', self.body)
        self.assertIn('<span class="es-icon">🎯</span>暂无推荐', self.js)

    def test_supply_empty_states_have_icons(self):
        self.assertIn('EMPTY("✅", "库存充足，无预警")', self.js)
        self.assertIn('EMPTY("🚚", "无运单数据")', self.js)

    def test_market_empty_states_have_icons(self):
        self.assertIn('EMPTY("📊", "暂无品类数据")', self.js)
        self.assertIn('EMPTY("🎯", "暂无选品建议")', self.js)


class TestCardHover(UIStaticTestBase):
    """优化 6：卡片 hover 效果。"""

    def test_card_hover_shadow(self):
        self.assertIn(".card:hover", self.css)
        self.assertIn("0 4px 12px", self.css)

    def test_rec_card_hover_lift(self):
        self.assertIn(".rec-card:hover", self.css)
        self.assertIn("translateY(-2px)", self.css)
        self.assertIn("0 8px 20px", self.css)


class TestNavActiveIndicator(UIStaticTestBase):
    """优化 7：侧边栏导航 active 左侧色条。"""

    def test_active_before_pseudo_defined(self):
        self.assertIn(".nav-item.active::before", self.css)

    def test_active_bar_is_primary_color(self):
        # ::before 色条使用主色且宽度 3px
        self.assertIn("width: 3px", self.css)
        self.assertIn("background: var(--primary)", self.css)

    def test_four_nav_items_present(self):
        for page in ("market", "service", "supply", "rec"):
            self.assertIn(f'data-page="{page}"', self.body)


class TestHTMLStructure(UIStaticTestBase):
    """关键 DOM 结构完整（JS 依赖的 id 必须存在）。"""

    def test_four_pages_present(self):
        for pid in ("page-market", "page-service", "page-supply", "page-rec"):
            self.assertIn(f'id="{pid}"', self.body)

    def test_js_dependency_ids_present(self):
        for el_id in (
            "trendingBody", "pickBody", "supplyKpis", "invBody", "shipBody",
            "messages", "recGrid", "quickBar", "input", "sendBtn", "toast",
            "customerSelect", "recCustomer", "healthDot", "healthText",
            "insightCard", "riskCard", "etaResult", "descResult",
        ):
            self.assertIn(f'id="{el_id}"', self.body)


class TestJSLogicStructure(UIStaticTestBase):
    """关键 JS 逻辑的结构性断言。"""

    def test_parse_sse_handles_event_and_data_lines(self):
        self.assertIn('line.startsWith("event:")', self.js)
        self.assertIn('line.startsWith("data:")', self.js)
        self.assertIn("JSON.parse(dataStr)", self.js)

    def test_send_message_handles_all_sse_events(self):
        # 流式回复解析必须覆盖 4 类事件
        for ev in ('"sources"', '"intent"', '"token"', '"done"'):
            self.assertIn(f"ev.event === {ev}", self.js)

    def test_intent_class_covers_all_five_intents(self):
        for intent in ("物流", "退款", "库存", "关税", "其他"):
            self.assertIn(f'"{intent}":', self.js)

    def test_ship_status_map_complete(self):
        for status in ("delivered", "in_transit", "customs", "delayed", "pending"):
            self.assertIn(f"{status}:", self.js)

    def test_risk_badge_levels(self):
        for level in ("高风险", "中风险"):
            self.assertIn(f'lv === "{level}"', self.js)

    def test_esc_helper_defined_for_xss_safety(self):
        self.assertIn("const esc = s =>", self.js)

    def test_enter_to_send_shift_enter_newline(self):
        self.assertIn('e.key === "Enter" && !e.shiftKey', self.js)

    def test_intent_map_matches_backend(self):
        """前端意图映射与后端意图归一化保持一致。"""
        backend = read(BACKEND_CS)
        for intent in ("物流", "退款", "库存", "关税"):
            self.assertIn(f'"{intent}"', backend)
        self.assertIn('"其他"', self.js)


if __name__ == "__main__":
    unittest.main()
