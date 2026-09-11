"""智能供应链 API。

规则评分 + LLM 双引擎：
    - 库存预警 / 物流风险 / ETA 预测（基于历史均值）
    - LLM 风险预警报告：DeepSeek 汇总生成可读分析 + 处置建议
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.v1.deps import get_session
from app.core.config import settings
from app.core.logging import logger
from app.db.models import Inventory, Product, Shipment, Warehouse
from app.integrations.llm.deepseek import LLMError, chat_json

router = APIRouter()


# ---------- Schemas ----------
class InventoryAlert(BaseModel):
    inventory_id: int
    product_id: int
    product_name: str
    warehouse_code: str
    stock: int
    safety_stock: int
    gap: int
    in_transit: int
    suggestion: str


class ShipmentRiskItem(BaseModel):
    shipment_id: int
    route: str
    carrier: str
    status: str
    risk_score: float
    level: str
    action: str


class ETAPrediction(BaseModel):
    shipment_id: int
    carrier: str
    route: str
    predicted_days: float
    confidence: str


# ---------- Endpoints ----------
@router.get("/inventory/alerts", response_model=List[InventoryAlert], summary="库存预警")
def inventory_alerts(db: Session = Depends(get_session)):
    rows = (
        db.query(Inventory, Product, Warehouse)
        .join(Product, Inventory.product_id == Product.id)
        .join(Warehouse, Inventory.warehouse_id == Warehouse.id)
        .filter(Inventory.stock < Inventory.safety_stock)
        .all()
    )
    out = []
    for inv, prod, wh in rows:
        gap = inv.safety_stock - inv.stock
        if inv.in_transit >= gap:
            suggestion = "在途补货可覆盖缺口，无需紧急下单"
        else:
            suggestion = f"缺口 {gap}，建议补货 {gap * 2} 件"
        out.append(InventoryAlert(
            inventory_id=inv.id,
            product_id=prod.id,
            product_name=prod.name,
            warehouse_code=wh.code,
            stock=inv.stock,
            safety_stock=inv.safety_stock,
            gap=gap,
            in_transit=inv.in_transit,
            suggestion=suggestion,
        ))
    return out


@router.get("/shipments/risk", response_model=List[ShipmentRiskItem], summary="物流风险监控")
def shipment_risks(db: Session = Depends(get_session)):
    shipments = db.query(Shipment).all()
    out = []
    for s in shipments:
        if s.risk_score >= 0.7:
            level, action = "高风险", "立即联系承运商核实，并通知客户可能延误"
        elif s.risk_score >= 0.4:
            level, action = "中风险", "持续跟踪，预留备选承运商"
        else:
            level, action = "低风险", "正常跟踪"
        out.append(ShipmentRiskItem(
            shipment_id=s.id,
            route=f"{s.origin_country}->{s.dest_country}",
            carrier=s.carrier,
            status=s.status,
            risk_score=s.risk_score,
            level=level,
            action=action,
        ))
    out.sort(key=lambda x: x.risk_score, reverse=True)
    return out


@router.get("/logistics/eta/{shipment_id}", response_model=ETAPrediction, summary="运输时效预测")
def predict_eta(shipment_id: int, db: Session = Depends(get_session)):
    ship = db.get(Shipment, shipment_id)
    if not ship:
        raise HTTPException(404, "运单不存在")

    # 取同路线同承运商的历史均值（移动平均思想）
    hist = (
        db.query(Shipment.est_days)
        .filter(
            Shipment.origin_country == ship.origin_country,
            Shipment.dest_country == ship.dest_country,
            Shipment.carrier == ship.carrier,
            Shipment.status == "delivered",
        )
        .all()
    )
    days_list = [r[0] for r in hist]
    if days_list:
        predicted = round(sum(days_list) / len(days_list), 1)
        confidence = "high" if len(days_list) >= 5 else "medium"
    else:
        # 无历史样本：用同路线所有承运商均值兜底
        route_hist = (
            db.query(func.avg(Shipment.est_days))
            .filter(
                Shipment.origin_country == ship.origin_country,
                Shipment.dest_country == ship.dest_country,
            )
            .scalar()
        )
        predicted = round(float(route_hist or ship.est_days), 1)
        confidence = "low"
    return ETAPrediction(
        shipment_id=ship.id,
        carrier=ship.carrier,
        route=f"{ship.origin_country}->{ship.dest_country}",
        predicted_days=predicted,
        confidence=confidence,
    )


# ---------- LLM 风险预警报告 ----------
class RiskReport(BaseModel):
    inventory_alerts: List[InventoryAlert]
    shipment_risks: List[ShipmentRiskItem]
    report: str
    source: str  # llm / rule


SUPPLY_SYSTEM_PROMPT = """你是跨境电商供应链风险分析专家。

任务：基于库存预警和物流风险数据，生成一段供应链风险摘要。

要求：
- 总结整体风险等级（低/中/高）
- 突出最紧急的 1-2 个风险点
- 给出 1-2 条可执行处置建议（补货/换承运商/通知客户等）
- 用中文输出，不超过 200 字
- 严格输出 JSON: {"report": "..."}"""


@router.get("/risk-report", response_model=RiskReport, summary="LLM 供应链风险报告")
def risk_report(db: Session = Depends(get_session)):
    """汇总库存预警 + 物流风险，由 LLM 生成统一报告。"""
    inv_alerts = inventory_alerts(db)
    ship_risks = shipment_risks(db)

    report = ""
    source = "rule"
    if settings.llm_ready:
        inv_text = "\n".join(
            f"- {a.product_name} @ {a.warehouse_code}: 库存{a.stock} < 安全线{a.safety_stock}，缺口{a.gap}，在途{a.in_transit}"
            for a in inv_alerts
        ) or "无库存预警"
        ship_text = "\n".join(
            f"- 运单{s.shipment_id} {s.route} {s.carrier} 风险{s.risk_score}({s.level}) 状态{s.status}"
            for s in ship_risks[:10]
        ) or "无物流风险"
        try:
            data = chat_json(
                [{"role": "system", "content": SUPPLY_SYSTEM_PROMPT},
                 {"role": "user", "content":
                     f"库存预警:\n{inv_text}\n\n物流风险(top10):\n{ship_text}"}],
                temperature=0.5, max_tokens=400,
            )
            report = str(data.get("report", "")).strip()
            source = "llm"
            logger.info("LLM 供应链风险报告生成成功")
        except Exception as e:
            logger.warning(f"LLM 供应链风险报告失败，降级: {e}")
    if not report:
        high = sum(1 for s in ship_risks if s.level == "高风险")
        mid = sum(1 for s in ship_risks if s.level == "中风险")
        level = "高" if high > 0 else ("中" if mid > 0 or inv_alerts else "低")
        lines = [f"整体风险等级：{level}",
                 f"库存预警 {len(inv_alerts)} 条，物流高风险 {high} 条 / 中风险 {mid} 条"]
        if inv_alerts:
            lines.append(f"建议：立即补货 {inv_alerts[0].product_name}（缺口 {inv_alerts[0].gap}）")
        if high > 0:
            lines.append("建议：联系高风险运单承运商核实")
        report = "\n".join(lines)

    return RiskReport(
        inventory_alerts=inv_alerts, shipment_risks=ship_risks,
        report=report, source=source,
    )
