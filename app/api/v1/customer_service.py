"""智能客服 API。

实现：
    1. RAG 检索：用户问题先经 TF-IDF 知识库检索相关 FAQ，拼进 prompt
    2. LLM 生成：DeepSeek 生成意图分类 + 多语言回复
    3. 流式输出：/chat/stream 端点用 SSE 逐 token 推送
    4. 自动降级：LLM 失败时回落关键词规则
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, AsyncGenerator, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.deps import get_session
from app.core.config import settings
from app.core.logging import logger
from app.db.models import ChatSession, Customer
from app.integrations.llm.deepseek import LLMError, chat_json, chat_stream
from app.integrations.llm.knowledge_base import format_context

router = APIRouter()


# ---------- 降级用：关键词意图识别 ----------
INTENT_RULES = [
    ("物流", ["物流", "快递", "shipping", "delivery", "届かない", "配送"]),
    ("退款", ["退款", "退货", "refund", "return", "返金"]),
    ("库存", ["库存", "现货", "stock", "在庫", "有在"]),
    ("关税", ["关税", "清关", "customs", "duty", "関税"]),
]
REPLY_TEMPLATES = {
    "物流": {
        "en": "Your order is in transit. Standard cross-border delivery takes 7-15 days. You can track it via the logistics link in your account.",
        "ja": "ご注文は配送中です。越境配送は通常7〜15日かかります。アカウント内の追跡リンクからご確認いただけます。",
        "de": "Ihre Bestellung befindet sich auf dem Transport. Grenzüberschreitende Lieferungen dauern in der Regel 7-15 Tage.",
        "ar": "طلبك قيد الشحن. التوصيل الدولي يستغرق عادة 7-15 يوما.",
    },
    "退款": {
        "en": "Refund requests are reviewed within 48 hours. Approved refunds will be credited to your original payment method.",
        "ja": "返金申請は48時間以内に審査されます。承認された返金は元の支払い方法に返金されます。",
        "de": "Rückerstattungsanfragen werden innerhalb von 48 Stunden geprüft.",
        "ar": "سيتم مراجعة طلب استرداد المبلغ خلال 48 ساعة.",
    },
    "库存": {
        "en": "This product is currently in stock. You may proceed to checkout.",
        "ja": "この商品は在庫ありです。決済に進めます。",
        "de": "Dieses Produkt ist auf Lager.",
        "ar": "هذا المنتج متوفر حاليا.",
    },
    "关税": {
        "en": "Import duties are calculated at checkout based on destination country. You will see the final price before payment.",
        "ja": "輸入関税は決済時に配送先国に基づき計算され、支払前に最終価格が表示されます。",
        "de": "Einfuhrzölle werden an der Kasse basierend auf dem Zielland berechnet.",
        "ar": "يتم حساب رسوم الاستيراد عند الدفع بناء على بلد الوجهة.",
    },
    "default": {
        "en": "Thanks for your question. A human agent will follow up shortly. You can also check our help center.",
        "ja": "お問い合わせありがとうございます。担当者が追ってご連絡いたします。",
        "de": "Vielen Dank für Ihre Frage. Ein Mitarbeiter wird sich in Kürze melden.",
        "ar": "شكرا على سؤالك. سيتواصل معك وكيل بشري قريبا.",
    },
}


def _fallback_intent(text: str) -> str:
    text_lower = text.lower()
    for intent, keywords in INTENT_RULES:
        if any(k in text_lower for k in keywords):
            return intent
    return "default"


def _fallback_reply(intent: str, language: str) -> str:
    tpl = REPLY_TEMPLATES.get(intent, REPLY_TEMPLATES["default"])
    return tpl.get(language, tpl.get("en"))


# ---------- LLM 路径：RAG 检索 + 意图分类 + 多语言生成 ----------
SYSTEM_PROMPT = """你是一个跨境电商平台的智能客服助手。

任务：基于下方知识库内容，理解用户问题，输出 JSON，包含两个字段：
1. intent：意图分类，取值之一 ["物流","退款","库存","关税","其他"]
2. reply：用指定客户语言生成的、自然且专业的客服回复

要求：
- 必须使用指定客户语言回复
- 优先基于「知识库」中的内容作答，找不到相关条目时按通用经验回答并标注"建议联系人工客服"
- 回复简洁（≤100 字），友好专业
- 严格输出 JSON，不要 markdown、不要多余说明"""

SYSTEM_PROMPT_STREAM = """你是一个跨境电商平台的智能客服助手。

基于下方知识库内容，用指定客户语言回答用户问题。

要求：
- 优先基于「知识库」中的内容作答，找不到相关条目时按通用经验回答并标注"建议联系人工客服"
- 回复简洁（≤100 字），友好专业
- 直接输出回复正文，不要 markdown 标题，不要说明"""

LANG_NAME = {
    "en": "English",
    "ja": "日本語",
    "de": "Deutsch",
    "ar": "العربية",
}


def _build_messages(message: str, language: str, history: list[dict[str, Any]],
                    for_stream: bool = False) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """构建 LLM messages：system（含 RAG 上下文）+ 历史 + 当前问题。

    返回 (messages, sources) —— sources 为检索到的 FAQ 引用，用于前端展示。
    """
    lang_name = LANG_NAME.get(language, "English")
    context, sources = format_context(message, top_k=3)

    sys_tpl = SYSTEM_PROMPT_STREAM if for_stream else SYSTEM_PROMPT
    sys_content = sys_tpl
    if context:
        sys_content += f"\n\n=== 知识库（检索到的相关 FAQ）===\n{context}"
    sys_content += f"\n\n=== 客户语言: {lang_name} ==="

    messages: list[dict[str, str]] = [{"role": "system", "content": sys_content}]

    recent = [m for m in history if m.get("role") in ("user", "bot")][-6:]
    for m in recent:
        role = "user" if m["role"] == "user" else "assistant"
        messages.append({"role": role, "content": m["content"]})

    messages.append({"role": "user", "content": message})
    return messages, sources


def _llm_reply(message: str, language: str, history: list[dict[str, Any]]) -> tuple[str, str, list[dict[str, Any]]]:
    """非流式：一次调用拿 {intent, reply}。失败抛 LLMError。返回 (intent, reply, sources)。"""
    messages, sources = _build_messages(message, language, history, for_stream=False)
    data = chat_json(messages, temperature=0.3, max_tokens=400)
    intent = str(data.get("intent", "其他"))
    reply = str(data.get("reply", "")).strip()
    if not reply:
        raise LLMError("LLM 返回空回复")
    if intent not in ("物流", "退款", "库存", "关税"):
        intent = "其他"
    return intent, reply, sources


def _llm_intent(message: str, language: str, history: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """仅分类意图（流式前先调用，快）。返回 (intent, sources)。失败抛 LLMError。"""
    messages, sources = _build_messages(message, language, history, for_stream=False)
    data = chat_json(messages, temperature=0.0, max_tokens=100)
    intent = str(data.get("intent", "其他"))
    if intent not in ("物流", "退款", "库存", "关税"):
        intent = "其他"
    return intent, sources


def generate_reply(message: str, language: str, history: list[dict[str, Any]]) -> tuple[str, str, str, list[dict[str, Any]]]:
    """统一入口：优先 LLM（含 RAG），失败降级关键词规则。

    返回 (intent, reply, source, sources)
    """
    if settings.llm_ready:
        try:
            intent, reply, sources = _llm_reply(message, language, history)
            logger.info(f"LLM(RAG) 回复成功 intent={intent} lang={language}")
            return intent, reply, "llm", sources
        except LLMError as e:
            logger.warning(f"LLM 调用失败，降级关键词规则: {e}")
        except Exception as e:
            logger.warning(f"LLM 未知异常，降级关键词规则: {e}")
    intent = _fallback_intent(message)
    reply = _fallback_reply(intent, language)
    # 降级路径仍可提供检索来源（仅作展示用，未参与生成）
    _, sources = format_context(message, top_k=3)
    return intent, reply, "rule", sources


# ---------- Schemas ----------
class CustomerCreate(BaseModel):
    name: str
    country: str = "US"
    language: str = "en"
    email: Optional[str] = None


class CustomerOut(BaseModel):
    id: int
    name: str
    country: str
    language: str
    email: Optional[str] = None

    class Config:
        from_attributes = True


class ChatRequest(BaseModel):
    customer_id: int
    message: str


class SourceRef(BaseModel):
    id: str
    category: str
    question: str
    answer: str
    score: float


class ChatResponse(BaseModel):
    session_id: int
    intent: str
    reply: str
    language: str
    timestamp: str
    source: str = "llm"
    sources: list[SourceRef] = []  # RAG 检索到的 FAQ 引用


class MessageItem(BaseModel):
    role: str
    content: str
    ts: str


# ---------- 端点 ----------
@router.get("/customers", response_model=List[CustomerOut], summary="客户列表")
def list_customers(db: Session = Depends(get_session)):
    return db.query(Customer).order_by(Customer.id.desc()).all()


@router.post("/customers", response_model=CustomerOut, summary="创建客户")
def create_customer(req: CustomerCreate, db: Session = Depends(get_session)):
    customer = Customer(**req.model_dump())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("/customers/{customer_id}/sessions", summary="客户的会话列表")
def list_customer_sessions(customer_id: int, db: Session = Depends(get_session)):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "客户不存在")
    return (
        db.query(ChatSession)
        .filter(ChatSession.customer_id == customer_id)
        .order_by(ChatSession.id.desc())
        .all()
    )


def _get_or_create_session(db: Session, customer_id: int, lang: str) -> tuple[ChatSession, list[dict[str, Any]]]:
    """取 open 会话，没有则新建。返回 (session, history)。"""
    session = (
        db.query(ChatSession)
        .filter(ChatSession.customer_id == customer_id, ChatSession.status == "open")
        .first()
    )
    history = list(session.messages) if session and session.messages else []
    if not session:
        session = ChatSession(
            customer_id=customer_id, language=lang, status="open", messages=[]
        )
        db.add(session)
    return session, history


@router.post("/chat", response_model=ChatResponse, summary="智能客服对话（DeepSeek + RAG）")
def chat(req: ChatRequest, db: Session = Depends(get_session)):
    customer = db.get(Customer, req.customer_id)
    if not customer:
        raise HTTPException(404, "客户不存在")
    lang = customer.language or "en"

    session, history = _get_or_create_session(db, req.customer_id, lang)
    now = datetime.utcnow().isoformat()
    intent, reply, source, sources = generate_reply(req.message, lang, history)

    new_messages = list(history)
    new_messages.append({"role": "user", "content": req.message, "ts": now})
    new_messages.append({"role": "bot", "content": reply, "ts": now})
    session.messages = new_messages
    db.commit()
    db.refresh(session)

    return ChatResponse(
        session_id=session.id, intent=intent, reply=reply,
        language=lang, timestamp=now, source=source,
        sources=[SourceRef(**s) for s in sources],
    )


@router.post("/chat/stream", summary="智能客服流式对话（SSE）")
async def chat_stream_endpoint(req: ChatRequest, db: Session = Depends(get_session)):
    """SSE 流式回复。

    事件流：
        event: sources data: [{"id":"kb001",...}]        （RAG 检索到的 FAQ 引用）
        event: intent   data: {"intent":"物流"}
        event: token    data: {"content":"您的"}          （多次）
        event: done     data: {"session_id":1,"source":"llm","language":"en","sources":[...]}

    LLM 不可用时自动降级为单条 token 事件（非流式）。
    """
    customer = db.get(Customer, req.customer_id)
    if not customer:
        raise HTTPException(404, "客户不存在")
    lang = customer.language or "en"
    session, history = _get_or_create_session(db, req.customer_id, lang)
    now = datetime.utcnow().isoformat()

    async def event_gen() -> AsyncGenerator[str, None]:
        def sse(event: str, data: Any) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

        full_reply = ""
        source = "llm"

        if settings.llm_ready:
            # 1. 先快速分类意图（同步调用，顺便拿到 RAG sources）
            try:
                intent, sources = _llm_intent(req.message, lang, history)
            except Exception as e:
                logger.warning(f"流式意图分类失败，降级关键词: {e}")
                intent = _fallback_intent(req.message)
                _, sources = format_context(req.message, top_k=3)
            yield sse("sources", sources)
            yield sse("intent", {"intent": intent})

            # 2. 流式生成回复
            try:
                messages, _ = _build_messages(req.message, lang, history, for_stream=True)
                for token in chat_stream(messages, temperature=0.5, max_tokens=400):
                    full_reply += token
                    yield sse("token", {"content": token})
            except Exception as e:
                logger.warning(f"流式生成失败，降级模板: {e}")
                if not full_reply:
                    full_reply = _fallback_reply(intent, lang)
                    source = "rule"
                    yield sse("token", {"content": full_reply})
        else:
            # LLM 未配置：直接降级
            intent = _fallback_intent(req.message)
            full_reply = _fallback_reply(intent, lang)
            source = "rule"
            _, sources = format_context(req.message, top_k=3)
            yield sse("sources", sources)
            yield sse("intent", {"intent": intent})
            yield sse("token", {"content": full_reply})

        # 3. 落库 + done
        new_messages = list(history)
        new_messages.append({"role": "user", "content": req.message, "ts": now})
        new_messages.append({"role": "bot", "content": full_reply, "ts": now})
        session.messages = new_messages
        db.commit()
        db.refresh(session)

        yield sse("done", {
            "session_id": session.id, "source": source,
            "language": lang, "intent": intent, "sources": sources,
        })

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/sessions/{session_id}", summary="获取会话历史")
def get_session_history(session_id: int, db: Session = Depends(get_session)):
    session = db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    return {
        "session_id": session.id,
        "customer_id": session.customer_id,
        "language": session.language,
        "status": session.status,
        "messages": session.messages or [],
    }


@router.post("/sessions/{session_id}/close", summary="关闭会话")
def close_session(session_id: int, db: Session = Depends(get_session)):
    session = db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    session.status = "closed"
    db.commit()
    return {"session_id": session.id, "status": "closed"}
