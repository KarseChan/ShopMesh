"""支付服务(购物功能 P3)—— 沙箱/mock,演示正确的支付架构,不接真实资金。

对应 docs/purchase-feature-design.md 的支付红线:
- 后端/agent 永不接触卡号/CVV/密码;真实接入时用支付网关的 hosted checkout。
- 订单转为 paid **只信 webhook**(支付方 → 后端),且**验签**,防伪造。
- webhook 幂等:重复回调不重复处理。
- mock 用一个"模拟支付成功"入口生成**已签名**的 webhook,走和真实一样的验签路径。
"""

import hashlib
import hmac
import json
import time
import uuid

from src.config import config
from src.db.redis_client import get_redis
from src.observability.logger import get_logger
from src.skills import order_service

logger = get_logger("payment_service")

_TTL = 3600  # 支付会话 1h


def _secret() -> bytes:
    return str(config.get("payment", {}).get("webhook_secret", "dev-pay-secret")).encode()


def _sign(body: bytes) -> str:
    return hmac.new(_secret(), body, hashlib.sha256).hexdigest()


def verify(body: bytes, signature: str) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(_sign(body), signature)


def _pay_key(ref: str) -> str:
    return f"payment:{ref}"


async def create_session(order_id: str, amount: float) -> dict:
    """创建支付会话,返回 payment_ref + 支付跳转地址(mock 结账页)。"""
    r = get_redis()
    ref = f"PAY-{uuid.uuid4().hex[:16].upper()}"
    await r.set(_pay_key(ref), json.dumps({
        "order_id": order_id, "amount": amount, "status": "pending",
        "created_at": int(time.time()),
    }), ex=_TTL)
    logger.info("payment_session_created", ref=ref, order_id=order_id, amount=amount)
    # 真实场景这里返回支付方的 hosted checkout URL;mock 返回本地模拟页
    return {"ok": True, "payment_ref": ref, "amount": amount,
            "pay_url": f"/api/payments/{ref}/simulate"}


async def handle_webhook(raw_body: bytes, signature: str) -> dict:
    """支付方回调入口:验签 → 幂等地把订单标记 paid。"""
    if not verify(raw_body, signature):
        logger.warning("payment_webhook_bad_signature")
        return {"ok": False, "message": "签名校验失败"}
    try:
        evt = json.loads(raw_body)
    except Exception:
        return {"ok": False, "message": "非法回调"}

    ref = evt.get("payment_ref", "")
    if evt.get("event") != "payment.succeeded":
        return {"ok": True, "ignored": True}

    r = get_redis()
    raw = await r.get(_pay_key(ref))
    if not raw:
        return {"ok": False, "message": "支付会话不存在或已过期"}
    sess = json.loads(raw)
    if sess.get("status") == "succeeded":
        return {"ok": True, "idempotent": True}  # 幂等:重复回调

    res = await order_service.mark_paid(sess["order_id"])
    if res.get("ok"):
        sess["status"] = "succeeded"
        await r.set(_pay_key(ref), json.dumps(sess), ex=_TTL)
    logger.info("payment_webhook_processed", ref=ref, order_id=sess["order_id"], paid=res.get("ok"))
    return {"ok": res.get("ok", False), "order_id": sess["order_id"], "status": res.get("status")}


async def simulate_payment(payment_ref: str) -> dict:
    """【mock 支付方】模拟用户在支付页完成付款 —— 生成已签名 webhook 并走验签路径。

    真实场景由支付网关服务器回调;这里用它替代,证明 webhook 链路正确。
    """
    r = get_redis()
    raw = await r.get(_pay_key(payment_ref))
    if not raw:
        return {"ok": False, "message": "支付会话不存在"}
    payload = json.dumps({"event": "payment.succeeded", "payment_ref": payment_ref},
                         ensure_ascii=False).encode()
    signature = _sign(payload)
    return await handle_webhook(payload, signature)
