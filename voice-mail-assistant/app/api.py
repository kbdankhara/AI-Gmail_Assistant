"""JSON endpoints used by the screens and the voice loop."""
import json
import re
from functools import wraps

from flask import (Blueprint, Response, current_app, g, jsonify, request,
                   session)

from . import ai, billing, crypto, gmail
from .auth import api_user
from .config import CONSENT_ITEMS, PROVIDERS, VOICE_LANGS
from .models import AIKey, Consent, GmailAccount, User, utcnow
from .pages import app_state

bp = Blueprint("api", __name__)

MAX_GMAIL_ACCOUNTS = 5
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def _json() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _fail(message: str, code: int = 400):
    return jsonify(error=message), code


def needs_access(view):
    """Mail endpoints need an active trial or subscription once billing is switched on."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not billing.has_access(current_app.cfg, g.user):
            return _fail("Your trial or subscription isn't active. Open Payment to continue.", 402)
        return view(*args, **kwargs)
    return wrapped


def _account(account_id) -> GmailAccount | None:
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return None
    return next((a for a in g.user.gmail_accounts if a.id == account_id), None)


def _ai_creds(user):
    provider = user.ai_provider if user.ai_provider in PROVIDERS else "anthropic"
    key = next((k for k in user.ai_keys if k.provider == provider), None)
    if key is None:
        return None
    return provider, crypto.decrypt(key.enc_key), user.ai_model or PROVIDERS[provider]["model"]


# ---------------------------------------------------------------- consent

@bp.post("/api/consent")
@api_user(consent=False)
def accept_consent():
    accepted = set(_json().get("items") or [])
    required = {i["key"] for i in CONSENT_ITEMS}
    if accepted != required:
        return _fail("All four permissions are required to use the service.")
    current_app.db.add(Consent(user_id=g.user.id, policy_version=current_app.cfg.POLICY_VERSION,
                               items=",".join(sorted(required)), ip=(request.remote_addr or "")[:64]))
    current_app.db.commit()
    return jsonify(ok=True, next="/profile?onboarding=1" if not g.user.setup_done else "/assistant")


# ---------------------------------------------------------------- profile

@bp.get("/api/state")
@api_user()
def state():
    return jsonify(app_state(g.user))


@bp.post("/api/gmail-accounts")
@api_user()
def save_gmail_account():
    data = _json()
    address = str(data.get("address") or "").strip().lower()
    password = re.sub(r"\s+", "", str(data.get("app_password") or ""))
    if not EMAIL_RE.match(address) or len(address) > 320:
        return _fail("Enter a valid Gmail address.")
    if password and not re.fullmatch(r"[A-Za-z]{16}", password):
        return _fail("An App Password is 16 letters. Copy it exactly as Google shows it.")

    db = current_app.db
    account = _account(data.get("id")) if data.get("id") else None
    if data.get("id") and account is None:
        return _fail("That account no longer exists.", 404)
    clash = next((a for a in g.user.gmail_accounts if a.address == address and a is not account), None)
    if clash:
        return _fail("That Gmail address is already connected.")
    if account is None:
        if not password:
            return _fail("Enter the App Password for this address.")
        if len(g.user.gmail_accounts) >= MAX_GMAIL_ACCOUNTS:
            return _fail(f"You can connect up to {MAX_GMAIL_ACCOUNTS} Gmail accounts.")
        account = GmailAccount(user_id=g.user.id, address=address)
        g.user.gmail_accounts.append(account)
    elif account.address != address and not password:
        return _fail("Enter the App Password for the new address.")
    account.address = address
    if password:
        account.enc_password = crypto.encrypt(password)
    db.commit()
    return jsonify(ok=True, account={"id": account.id, "address": account.address, "saved": True})


@bp.delete("/api/gmail-accounts/<int:account_id>")
@api_user()
def delete_gmail_account(account_id):
    account = _account(account_id)
    if account is None:
        return _fail("That account no longer exists.", 404)
    g.user.gmail_accounts.remove(account)
    current_app.db.commit()
    return jsonify(ok=True)


@bp.post("/api/gmail-accounts/<int:account_id>/test")
@api_user()
def test_gmail_account(account_id):
    account = _account(account_id)
    if account is None or not account.enc_password:
        return _fail("Save the address and App Password first.", 404)
    if current_app.cfg.DEMO_MODE:
        return jsonify(ok=True, message="Connected (demo inbox).")
    try:
        gmail.test_login(account.address, crypto.decrypt(account.enc_password))
    except gmail.GmailError as exc:
        return _fail(str(exc), 422)
    return jsonify(ok=True, message="Connected.")


@bp.post("/api/ai")
@api_user()
def save_ai():
    data = _json()
    provider = str(data.get("provider") or "")
    model = str(data.get("model") or "").strip()
    key = str(data.get("api_key") or "").strip()
    if provider not in PROVIDERS:
        return _fail("Choose an AI provider.")
    if model and not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", model):
        return _fail("That model name isn't valid.")
    if len(key) > 400 or re.search(r"\s", key):
        return _fail("That API key doesn't look right.")
    existing = next((k for k in g.user.ai_keys if k.provider == provider), None)
    if key:
        if existing:
            existing.enc_key = crypto.encrypt(key)
        else:
            g.user.ai_keys.append(AIKey(user_id=g.user.id, provider=provider, enc_key=crypto.encrypt(key)))
    elif existing is None:
        return _fail("Enter an API key for this provider.")
    g.user.ai_provider = provider
    g.user.ai_model = model or PROVIDERS[provider]["model"]
    current_app.db.commit()
    return jsonify(ok=True)


@bp.post("/api/ai/test")
@api_user()
def test_ai():
    creds = _ai_creds(g.user)
    if creds is None:
        return _fail("Save an API key for this provider first.", 404)
    if current_app.cfg.DEMO_MODE:
        return jsonify(ok=True, message="Connected (demo AI).")
    try:
        ai.test_connection(*creds)
    except ai.AIError as exc:
        return _fail(str(exc), 422)
    return jsonify(ok=True, message="Connected.")


@bp.post("/api/settings")
@api_user()
def save_settings():
    data = _json()
    lang = str(data.get("voice_lang") or "")
    if lang not in VOICE_LANGS:
        return _fail("Choose a voice language.")
    g.user.voice_lang = lang
    provider, model = str(data.get("provider") or ""), str(data.get("model") or "").strip()
    if provider in PROVIDERS:
        if model and not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", model):
            return _fail("That model name isn't valid.")
        g.user.ai_provider = provider
        g.user.ai_model = model or PROVIDERS[provider]["model"]
    if not any(a.enc_password for a in g.user.gmail_accounts):
        return _fail("Connect at least one Gmail account before continuing.")
    g.user.setup_done = True
    current_app.db.commit()
    return jsonify(ok=True, next="/assistant")


# ---------------------------------------------------------------- mail

def _mail_error(exc):
    return _fail(str(exc), 422)


@bp.get("/api/inbox")
@api_user()
@needs_access
def inbox():
    accounts = [a for a in g.user.gmail_accounts if a.enc_password]
    if not accounts:
        return _fail("Connect a Gmail account in Profile settings first.", 409)
    emails, errors = [], []
    for account in accounts:
        try:
            if current_app.cfg.DEMO_MODE:
                found = gmail.demo_list_unread(account.id)
            else:
                found = gmail.list_unread(account.address, crypto.decrypt(account.enc_password))
        except (gmail.GmailError, ValueError) as exc:
            errors.append({"address": account.address, "error": str(exc)})
            continue
        for item in found:
            emails.append({**item, "account_id": account.id, "account": account.address})
    if errors and len(errors) == len(accounts):
        return _fail(errors[0]["error"], 422)
    return jsonify(emails=emails, errors=errors, accounts=len(accounts), checked_at=utcnow().isoformat() + "Z")


def _load_mail(data):
    account = _account(data.get("account_id"))
    if account is None or not account.enc_password:
        raise gmail.GmailError("That Gmail account is no longer connected.")
    uid = str(data.get("uid") or "")
    if current_app.cfg.DEMO_MODE:
        return account, None, gmail.demo_fetch(uid)
    password = crypto.decrypt(account.enc_password)
    return account, password, gmail.fetch(account.address, password, uid)


@bp.post("/api/email/summary")
@api_user()
@needs_access
def email_summary():
    try:
        _, _, mail = _load_mail(_json())
    except (gmail.GmailError, ValueError) as exc:
        return _mail_error(exc)
    creds = None if current_app.cfg.DEMO_MODE else _ai_creds(g.user)
    notice = None
    if creds:
        try:
            summary = ai.summarize(*creds, mail)
        except ai.AIError as exc:
            summary, notice = ai.plain_summary(mail), str(exc)
    elif current_app.cfg.DEMO_MODE:
        summary = gmail.DEMO_SUMMARIES.get(mail.uid) or ai.plain_summary(mail)
    else:
        summary = ai.plain_summary(mail)
    return jsonify(summary=summary, notice=notice, used_ai=bool(creds and not notice))


@bp.post("/api/email/draft")
@api_user()
@needs_access
def email_draft():
    data = _json()
    instruction = str(data.get("instruction") or "").strip()[:2000]
    if not instruction:
        return _fail("I didn't catch what you'd like to say.")
    try:
        _, _, mail = _load_mail(data)
    except (gmail.GmailError, ValueError) as exc:
        return _mail_error(exc)
    creds = None if current_app.cfg.DEMO_MODE else _ai_creds(g.user)
    notice = None
    if creds:
        try:
            draft = ai.draft_reply(*creds, mail, instruction, g.user.name or g.user.first_name)
        except ai.AIError as exc:
            draft, notice = ai.plain_reply(mail, instruction, g.user.first_name), str(exc)
    else:
        draft = ai.plain_reply(mail, instruction, g.user.first_name)
    return jsonify(draft=draft, notice=notice)


@bp.post("/api/email/send")
@api_user()
@needs_access
def email_send():
    """Sends a reply the user has heard and approved, then marks the original as read."""
    data = _json()
    body = str(data.get("body") or "").strip()
    if not body or len(body) > 10000:
        return _fail("The reply is empty or too long.")
    if data.get("approved") is not True:
        return _fail("A reply is only sent after you approve it.")
    try:
        account, password, mail = _load_mail(data)
        if current_app.cfg.DEMO_MODE:
            gmail.demo_sent.append({"to": mail.reply_to, "subject": mail.subject, "body": body})
            gmail.demo_mark_read(account.id, mail.uid)
        else:
            gmail.send_reply(account.address, password, g.user.name, mail, body)
            try:
                gmail.mark_read(account.address, password, mail.uid)
            except gmail.GmailError:
                return jsonify(ok=True, marked_read=False)
    except (gmail.GmailError, ValueError) as exc:
        return _mail_error(exc)
    return jsonify(ok=True, marked_read=True)


@bp.post("/api/email/mark-read")
@api_user()
@needs_access
def email_mark_read():
    data = _json()
    account = _account(data.get("account_id"))
    if account is None or not account.enc_password:
        return _fail("That Gmail account is no longer connected.", 422)
    try:
        if current_app.cfg.DEMO_MODE:
            gmail.demo_mark_read(account.id, str(data.get("uid") or ""))
        else:
            gmail.mark_read(account.address, crypto.decrypt(account.enc_password), str(data.get("uid") or ""))
    except (gmail.GmailError, ValueError) as exc:
        return _mail_error(exc)
    return jsonify(ok=True)


# ---------------------------------------------------------------- billing

@bp.post("/api/billing/subscribe")
@api_user()
def billing_subscribe():
    cfg, user = current_app.cfg, g.user
    if not cfg.razorpay_configured:
        return _fail("Payments aren't switched on for this deployment yet.", 503)
    if user.sub_status in ("trialing", "active") and not user.cancel_requested:
        return _fail("You already have an active plan.")
    try:
        sub = billing.create_subscription(cfg, user, with_trial=not user.trial_used)
    except billing.BillingError as exc:
        return _fail(str(exc), 502)
    session["pending_sub"] = sub["id"]
    return jsonify(subscription_id=sub["id"], key_id=cfg.RAZORPAY_KEY_ID, name=cfg.APP_NAME,
                   description=f"{cfg.PLAN_PRICE_DISPLAY} / month" +
                               (f" after a {cfg.TRIAL_DAYS}-day free trial" if not user.trial_used else ""),
                   prefill={"name": user.name, "email": user.email})


@bp.post("/api/billing/verify")
@api_user()
def billing_verify():
    cfg, user, data = current_app.cfg, g.user, _json()
    sub_id = str(data.get("razorpay_subscription_id") or "")
    if not sub_id or sub_id != session.get("pending_sub"):
        return _fail("That payment doesn't match this session. Please try again.")
    if not billing.verify_checkout(cfg, str(data.get("razorpay_payment_id") or ""), sub_id,
                                   str(data.get("razorpay_signature") or "")):
        return _fail("The payment couldn't be verified. You have not been charged for a plan.")
    try:
        sub = billing.fetch_subscription(cfg, sub_id)
    except billing.BillingError as exc:
        return _fail(str(exc), 502)
    if sub.get("status") == "created":
        # Signature is valid, Razorpay just hasn't flipped the status yet.
        future = sub.get("start_at") and int(sub["start_at"]) > utcnow().timestamp()
        sub["status"] = "authenticated" if future else "active"
    user.sub_id, user.cancel_requested = sub_id, False
    billing.apply_subscription(user, sub)
    current_app.db.commit()
    session.pop("pending_sub", None)
    return jsonify(ok=True, billing=billing.summary(cfg, user))


@bp.post("/api/billing/cancel")
@api_user()
def billing_cancel():
    cfg, user = current_app.cfg, g.user
    if user.sub_status not in ("trialing", "active", "past_due") or not user.sub_id:
        return _fail("There's no active plan to cancel.")
    try:
        # A trial hasn't started a billing cycle, so it is cancelled right away;
        # a paid plan runs to the end of the cycle already paid for.
        sub = billing.cancel_subscription(cfg, user.sub_id, at_cycle_end=user.sub_status == "active")
    except billing.BillingError as exc:
        return _fail(str(exc), 502)
    user.cancel_requested = True
    billing.apply_subscription(user, sub)
    user.sub_status = "cancelled"
    current_app.db.commit()
    return jsonify(ok=True, billing=billing.summary(cfg, user))


@bp.post("/webhooks/razorpay")
def razorpay_webhook():
    cfg = current_app.cfg
    raw = request.get_data()
    if not billing.verify_webhook(cfg, raw, request.headers.get("X-Razorpay-Signature", "")):
        return _fail("Bad signature.", 400)
    try:
        sub_id = json.loads(raw)["payload"]["subscription"]["entity"]["id"]
    except (ValueError, KeyError, TypeError):
        return jsonify(ok=True, ignored=True)
    user = current_app.db.query(User).filter_by(sub_id=sub_id).one_or_none()
    if user is None:
        return jsonify(ok=True, ignored=True)
    try:
        # Events can arrive out of order, so read the subscription's current state.
        sub = billing.fetch_subscription(cfg, sub_id)
    except billing.BillingError:
        return _fail("Try again later.", 503)
    billing.apply_subscription(user, sub)
    current_app.db.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- privacy and data

@bp.get("/api/me/export")
@api_user(consent=False)
def export_data():
    user = g.user
    payload = {
        "exported_at": utcnow().isoformat() + "Z",
        "account": {"name": user.name, "email": user.email, "created_at": user.created_at.isoformat() + "Z"},
        "consents": [{"policy_version": c.policy_version, "items": c.items.split(","),
                      "accepted_at": c.accepted_at.isoformat() + "Z"} for c in user.consents],
        "gmail_accounts": [{"address": a.address, "app_password_saved": bool(a.enc_password)}
                           for a in user.gmail_accounts],
        "ai": {"provider": user.ai_provider, "model": user.ai_model,
               "keys_saved_for": sorted(k.provider for k in user.ai_keys)},
        "voice_language": user.voice_lang,
        "billing": billing.summary(current_app.cfg, user),
        "note": "Emails are never stored, so there are none to export. Secrets are not included.",
    }
    return Response(json.dumps(payload, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=voice-mail-assistant-data.json"})


@bp.post("/api/me/delete")
@api_user(consent=False)
def delete_me():
    cfg, user = current_app.cfg, g.user
    if _json().get("confirm") != "DELETE":
        return _fail("Type DELETE to confirm.")
    if cfg.razorpay_configured and user.sub_id and user.sub_status in ("trialing", "active", "past_due") \
            and not user.cancel_requested:
        try:
            billing.cancel_subscription(cfg, user.sub_id, at_cycle_end=False)
        except billing.BillingError:
            return _fail("Your plan couldn't be cancelled, so the account was not deleted. "
                         "Try again, or cancel the plan on the Payment page first.", 502)
    current_app.db.delete(user)
    current_app.db.commit()
    session.clear()
    return jsonify(ok=True)
