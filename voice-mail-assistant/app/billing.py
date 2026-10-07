"""Razorpay subscription: a free trial, then a monthly charge."""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import requests

from .models import utcnow

API = "https://api.razorpay.com/v1"
TIMEOUT = 20
TOTAL_BILLING_CYCLES = 120  # Razorpay needs an end; ten years of monthly cycles.


class BillingError(Exception):
    pass


def _request(cfg, method: str, path: str, payload: dict | None = None) -> dict:
    try:
        resp = requests.request(method, API + path, json=payload, timeout=TIMEOUT,
                                auth=(cfg.RAZORPAY_KEY_ID, cfg.RAZORPAY_KEY_SECRET))
    except requests.RequestException as exc:
        raise BillingError("Couldn't reach the payment provider. Try again.") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", {}).get("description", "")
        except Exception:
            detail = ""
        raise BillingError(f"The payment provider declined the request. {detail}".strip())
    return resp.json()


def create_plan(cfg) -> dict:
    return _request(cfg, "POST", "/plans", {
        "period": "monthly", "interval": 1,
        "item": {"name": f"{cfg.APP_NAME} monthly", "amount": cfg.PLAN_AMOUNT_MINOR,
                 "currency": cfg.PLAN_CURRENCY}})


def create_subscription(cfg, user, with_trial: bool) -> dict:
    payload = {"plan_id": cfg.RAZORPAY_PLAN_ID, "total_count": TOTAL_BILLING_CYCLES,
               "customer_notify": 1, "notes": {"user_id": str(user.id), "email": user.email}}
    if with_trial:
        start = datetime.now(timezone.utc) + timedelta(days=cfg.TRIAL_DAYS)
        payload["start_at"] = int(start.timestamp())
    return _request(cfg, "POST", "/subscriptions", payload)


def fetch_subscription(cfg, sub_id: str) -> dict:
    return _request(cfg, "GET", f"/subscriptions/{sub_id}")


def cancel_subscription(cfg, sub_id: str, at_cycle_end: bool) -> dict:
    return _request(cfg, "POST", f"/subscriptions/{sub_id}/cancel",
                    {"cancel_at_cycle_end": 1 if at_cycle_end else 0})


def _hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_checkout(cfg, payment_id: str, subscription_id: str, signature: str) -> bool:
    expected = _hmac(cfg.RAZORPAY_KEY_SECRET, f"{payment_id}|{subscription_id}".encode())
    return hmac.compare_digest(expected, signature or "")


def verify_webhook(cfg, raw_body: bytes, signature: str) -> bool:
    if not cfg.RAZORPAY_WEBHOOK_SECRET:
        return False
    return hmac.compare_digest(_hmac(cfg.RAZORPAY_WEBHOOK_SECRET, raw_body), signature or "")


def _ts(value) -> datetime | None:
    return datetime.fromtimestamp(int(value), timezone.utc).replace(tzinfo=None) if value else None


def apply_subscription(user, sub: dict) -> None:
    """Copy a Razorpay subscription's state onto the user."""
    status = sub.get("status")
    start_at, current_end = _ts(sub.get("start_at")), _ts(sub.get("current_end"))
    now = utcnow()
    if status == "authenticated":
        # Card authorised, first charge is in the future: this is the trial.
        user.sub_status = "trialing"
        user.trial_used = True
        user.trial_end = start_at
        user.access_until = start_at
    elif status == "active":
        # A cancellation at cycle end leaves Razorpay's status "active" until the cycle closes.
        user.sub_status = "cancelled" if user.cancel_requested else "active"
        user.trial_used = True
        user.access_until = current_end or user.access_until
    elif status in ("pending", "halted"):
        user.sub_status = "past_due"
    elif status in ("cancelled", "completed", "expired"):
        user.sub_status = "cancelled"
        # Keep what was already paid for (or the rest of the trial), nothing more.
        paid_until = current_end or user.access_until
        user.access_until = paid_until if paid_until and paid_until > now else now


def has_access(cfg, user) -> bool:
    if not cfg.razorpay_configured:
        return True  # billing is not switched on for this deployment
    if user.sub_status == "active":
        return True
    return bool(user.access_until and user.access_until > utcnow())


def summary(cfg, user) -> dict:
    return {
        "status": user.sub_status,
        "access": has_access(cfg, user),
        "enforced": cfg.razorpay_configured,
        "trial_available": not user.trial_used,
        "trial_end": user.trial_end.isoformat() + "Z" if user.trial_end else None,
        "access_until": user.access_until.isoformat() + "Z" if user.access_until else None,
    }
