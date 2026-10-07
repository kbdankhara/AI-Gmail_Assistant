import hashlib
import hmac
import json
import time

from app import ai, billing, crypto, gmail
from app.models import GmailAccount, User
from tests.conftest import Client


def test_pages_render_signed_out(client):
    for url in ("/", "/login", "/help", "/privacy", "/terms"):
        assert client.get(url).status_code == 200, url
    assert client.get("/nope").status_code == 404
    for url in ("/assistant", "/profile", "/payment", "/consent"):
        assert client.get(url).status_code == 302, url
    assert client.get("/api/state").status_code == 401


def test_onboarding_order(client):
    assert client.sign_in().headers["Location"].endswith("/consent")
    # Nothing works before consent.
    assert client.get("/assistant").headers["Location"].endswith("/consent")
    assert client.post("/api/gmail-accounts", {"address": "a@gmail.com", "app_password": "a" * 16}).status_code == 403
    # All four are required.
    assert client.post("/api/consent", {"items": ["terms", "gmail"]}).status_code == 400
    r = client.post("/api/consent", {"items": ["terms", "gmail", "ai", "voice"]})
    assert r.json["next"] == "/profile?onboarding=1"
    assert client.get("/assistant").headers["Location"].endswith("/profile?onboarding=1")
    # Can't finish set-up without a Gmail account.
    assert client.post("/api/settings", {"voice_lang": "en-GB"}).status_code == 400
    assert client.post("/api/gmail-accounts", {"address": "me@gmail.com", "app_password": "abcdefghijklmnop"}).status_code == 200
    assert client.post("/api/settings", {"voice_lang": "en-GB"}).json["next"] == "/assistant"
    for url in ("/assistant", "/profile", "/payment"):
        assert client.get(url).status_code == 200, url


def test_csrf_is_enforced(client):
    client.sign_in()
    assert client.post("/api/consent", {"items": ["terms", "gmail", "ai", "voice"]}, csrf=False).status_code == 400


def test_gmail_account_validation_and_limits(client):
    client.onboard()
    assert client.post("/api/gmail-accounts", {"address": "nope", "app_password": "a" * 16}).status_code == 400
    assert client.post("/api/gmail-accounts", {"address": "b@gmail.com", "app_password": "short"}).status_code == 400
    assert client.post("/api/gmail-accounts", {"address": "b@gmail.com"}).status_code == 400
    assert client.post("/api/gmail-accounts", {"address": "ME@gmail.com", "app_password": "a" * 16}).status_code == 400  # duplicate
    for i in range(4):
        assert client.post("/api/gmail-accounts", {"address": f"x{i}@gmail.com", "app_password": "a" * 16}).status_code == 200
    assert client.post("/api/gmail-accounts", {"address": "x9@gmail.com", "app_password": "a" * 16}).status_code == 400  # max 5
    state = client.get("/api/state").json
    assert len(state["gmail_accounts"]) == 5
    extra = state["gmail_accounts"][-1]["id"]
    assert client.delete(f"/api/gmail-accounts/{extra}").status_code == 200
    assert len(client.get("/api/state").json["gmail_accounts"]) == 4


def test_secrets_are_encrypted_and_never_returned(app, client):
    client.onboard()
    assert client.post("/api/ai", {"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "sk-ant-SECRETKEY"}).status_code == 200
    with app.app_context():
        user = app.db.query(User).one()
        stored = user.gmail_accounts[0].enc_password
        assert "abcdefghijklmnop" not in stored and crypto.decrypt(stored) == "abcdefghijklmnop"
        assert "SECRETKEY" not in user.ai_keys[0].enc_key
    for url in ("/api/state", "/api/me/export", "/profile"):
        body = client.get(url).get_data(as_text=True)
        assert "abcdefghijklmnop" not in body and "SECRETKEY" not in body, url
    # Blank password on update keeps the saved one.
    acc = client.get("/api/state").json["gmail_accounts"][0]
    assert client.post("/api/gmail-accounts", {"id": acc["id"], "address": acc["address"]}).status_code == 200
    with app.app_context():
        assert crypto.decrypt(app.db.query(GmailAccount).one().enc_password) == "abcdefghijklmnop"


def test_cannot_touch_another_users_account(app, client):
    client.onboard()
    with app.app_context():
        other = User(google_sub="other", email="other@example.com", name="Other")
        other.gmail_accounts.append(GmailAccount(address="other@gmail.com", enc_password=crypto.encrypt("z" * 16)))
        app.db.add(other)
        app.db.commit()
        other_acc = other.gmail_accounts[0].id
    assert client.delete(f"/api/gmail-accounts/{other_acc}").status_code == 404
    assert client.post(f"/api/gmail-accounts/{other_acc}/test").status_code == 404
    assert client.post("/api/gmail-accounts", {"id": other_acc, "address": "mine@gmail.com", "app_password": "a" * 16}).status_code == 404
    assert client.post("/api/email/summary", {"account_id": other_acc, "uid": "101"}).status_code == 422
    assert client.post("/api/email/mark-read", {"account_id": other_acc, "uid": "101"}).status_code == 422


def test_mail_flow_in_demo_mode(client):
    acc = client.onboard()
    inbox = client.get("/api/inbox").json
    assert [e["from"] for e in inbox["emails"]] == ["Priya Shah", "Daniel Okafor", "Acme Cloud Billing"]
    s = client.post("/api/email/summary", {"account_id": acc, "uid": "101"}).json
    assert "confirm the revised numbers before Friday" in s["summary"]
    d = client.post("/api/email/draft", {"account_id": acc, "uid": "101", "instruction": "the numbers look right, go ahead"}).json
    assert d["draft"].startswith("Hi Priya,") and "The numbers look right, go ahead." in d["draft"]
    # A reply is only sent with explicit approval.
    assert client.post("/api/email/send", {"account_id": acc, "uid": "101", "body": d["draft"]}).status_code == 400
    assert not gmail.demo_sent
    assert client.post("/api/email/send", {"account_id": acc, "uid": "101", "body": d["draft"], "approved": True}).status_code == 200
    assert gmail.demo_sent[0]["to"] == "priya.shah@example.com"
    assert client.post("/api/email/mark-read", {"account_id": acc, "uid": "102"}).status_code == 200
    assert [e["uid"] for e in client.get("/api/inbox").json["emails"]] == ["103"]


def test_reply_message_headers():
    original = gmail.demo_fetch("101")
    msg = gmail.build_reply("me@gmail.com", "Kevin D", original, "Hi Priya,\n\nLooks right.\n\nBest,\nKevin")
    assert msg["Subject"] == "Re: Q3 budget review"
    assert msg["To"] == "priya.shah@example.com" and msg["From"] == "Kevin D <me@gmail.com>"
    assert msg["In-Reply-To"] == "<demo-101@example.com>" and msg["References"] == "<demo-101@example.com>"


def test_email_parsing_strips_html_and_quotes():
    raw = (b"From: =?utf-8?q?Jos=C3=A9?= <jose@example.com>\r\nSubject: Hello\r\nMessage-ID: <1@x>\r\n"
           b"Content-Type: text/html; charset=utf-8\r\n\r\n"
           b"<html><style>p{}</style><p>Can we meet <b>Tuesday</b>?</p><script>x()</script>"
           b"<p>On Mon, 1 Jan 2026 at 10:00, Someone wrote:</p><p>old text</p></html>")
    mail = gmail._parse("7", raw)
    assert mail.sender_name == "José" and mail.sender_email == "jose@example.com"
    assert "Can we meet Tuesday ?" in mail.body.replace("  ", " ") or "Can we meet Tuesday?" in mail.body
    assert "old text" not in mail.body and "x()" not in mail.body


def test_ai_error_messages(monkeypatch):
    class Resp:
        status_code = 401
        def json(self): return {"error": {"message": "invalid x-api-key"}}
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: Resp())
    for provider in ("anthropic", "openai", "groq", "gemini"):
        try:
            ai.complete(provider, "bad", "some-model", "s", "u")
            assert False
        except ai.AIError as exc:
            assert "rejected the API key" in str(exc)


def test_export_and_delete_account(app, client):
    client.onboard()
    data = json.loads(client.get("/api/me/export").get_data(as_text=True))
    assert data["gmail_accounts"] == [{"address": "me@gmail.com", "app_password_saved": True}]
    assert data["consents"][0]["items"] == ["ai", "gmail", "terms", "voice"]
    assert client.post("/api/me/delete", {"confirm": "delete"}).status_code == 400
    assert client.post("/api/me/delete", {"confirm": "DELETE"}).status_code == 200
    with app.app_context():
        assert app.db.query(User).count() == 0 and app.db.query(GmailAccount).count() == 0
    assert client.get("/api/state").status_code == 401


def test_policy_change_asks_for_consent_again(app, client):
    client.onboard()
    app.cfg.POLICY_VERSION = "2099-01-01"
    assert client.get("/assistant").headers["Location"].endswith("/consent")
    assert client.get("/api/inbox").status_code == 403


# ---------------------------------------------------------------- billing

def _billing_app(tmp_path, monkeypatch):
    from app import create_app
    app = create_app({"DATABASE_URL": f"sqlite:///{tmp_path}/b.db", "DEMO_MODE": True, "DEV_LOGIN": True,
                      "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
                      "RAZORPAY_KEY_ID": "rzp_test_x", "RAZORPAY_KEY_SECRET": "secret",
                      "RAZORPAY_PLAN_ID": "plan_x", "RAZORPAY_WEBHOOK_SECRET": "whsec"})
    subs = {}

    def fake(cfg, method, path, payload=None):
        if method == "POST" and path == "/subscriptions":
            sub = {"id": f"sub_{len(subs) + 1}", "status": "created", "start_at": payload.get("start_at"), "current_end": None}
            subs[sub["id"]] = sub
            return dict(sub)
        sub = subs[path.split("/")[2]]
        if path.endswith("/cancel"):
            if not payload["cancel_at_cycle_end"]:
                sub["status"] = "cancelled"
        return dict(sub)

    monkeypatch.setattr(billing, "_request", fake)
    gmail.demo_reset()
    return app, subs


def _sign(secret, message):
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def test_trial_then_charge_then_cancel(tmp_path, monkeypatch):
    app, subs = _billing_app(tmp_path, monkeypatch)
    client = Client(app)
    client.onboard()
    # No plan yet: mail is locked.
    assert client.get("/api/inbox").status_code == 402

    r = client.post("/api/billing/subscribe").json
    sub_id = r["subscription_id"]
    assert subs[sub_id]["start_at"] > time.time() + 29 * 86400  # first charge is after the trial

    # A forged signature is rejected and unlocks nothing.
    bad = client.post("/api/billing/verify", {"razorpay_payment_id": "pay_1", "razorpay_subscription_id": sub_id, "razorpay_signature": "0" * 64})
    assert bad.status_code == 400 and client.get("/api/inbox").status_code == 402

    good = client.post("/api/billing/verify", {"razorpay_payment_id": "pay_1", "razorpay_subscription_id": sub_id,
                                               "razorpay_signature": _sign("secret", f"pay_1|{sub_id}")})
    assert good.json["billing"]["status"] == "trialing" and good.json["billing"]["access"]
    assert client.get("/api/inbox").status_code == 200

    # Webhook: unsigned is rejected; signed "charged" moves the plan to active.
    body = json.dumps({"event": "subscription.charged", "payload": {"subscription": {"entity": {"id": sub_id}}}})
    assert client.c.post("/webhooks/razorpay", data=body, headers={"X-Razorpay-Signature": "nope"}).status_code == 400
    subs[sub_id].update(status="active", current_end=int(time.time()) + 30 * 86400)
    assert client.c.post("/webhooks/razorpay", data=body, headers={"X-Razorpay-Signature": _sign("whsec", body)}).status_code == 200
    assert client.get("/api/state").json["billing"]["status"] == "active"

    # Cancel: access continues to the end of the paid month, and there's no second trial.
    c = client.post("/api/billing/cancel").json["billing"]
    assert c["status"] == "cancelled" and c["access"] and not c["trial_available"]
    assert client.get("/api/inbox").status_code == 200
    # A late webhook for the still-"active" subscription must not undo the cancellation.
    client.c.post("/webhooks/razorpay", data=body, headers={"X-Razorpay-Signature": _sign("whsec", body)})
    assert client.get("/api/state").json["billing"]["status"] == "cancelled"
    again = client.post("/api/billing/subscribe").json["subscription_id"]
    assert subs[again]["start_at"] is None


def test_failed_payment_locks_after_paid_period(tmp_path, monkeypatch):
    app, subs = _billing_app(tmp_path, monkeypatch)
    client = Client(app)
    client.onboard()
    sub_id = client.post("/api/billing/subscribe").json["subscription_id"]
    client.post("/api/billing/verify", {"razorpay_payment_id": "p", "razorpay_subscription_id": sub_id,
                                        "razorpay_signature": _sign("secret", f"p|{sub_id}")})
    body = json.dumps({"payload": {"subscription": {"entity": {"id": sub_id}}}})
    # Trial over, charge failed.
    subs[sub_id].update(status="halted", start_at=int(time.time()) - 60)
    with app.app_context():
        user = app.db.query(User).one()
        from datetime import timedelta
        from app.models import utcnow
        user.access_until = utcnow() - timedelta(minutes=1)
        app.db.commit()
    client.c.post("/webhooks/razorpay", data=body, headers={"X-Razorpay-Signature": _sign("whsec", body)})
    state = client.get("/api/state").json["billing"]
    assert state["status"] == "past_due" and not state["access"]
    assert client.get("/api/inbox").status_code == 402
