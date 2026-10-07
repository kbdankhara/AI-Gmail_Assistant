import pytest

from app import create_app, gmail
from app.models import Base


@pytest.fixture()
def app(tmp_path):
    app = create_app({"DATABASE_URL": f"sqlite:///{tmp_path}/test.db", "DEMO_MODE": True, "DEV_LOGIN": True,
                      "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
                      "RAZORPAY_KEY_ID": "", "RAZORPAY_KEY_SECRET": "", "RAZORPAY_PLAN_ID": "",
                      "RAZORPAY_WEBHOOK_SECRET": ""})
    gmail.demo_reset()
    yield app
    Base.metadata.drop_all(app.db.get_bind())


class Client:
    """Test client that carries the CSRF token like the browser does."""

    def __init__(self, app):
        self.c = app.test_client()

    def csrf(self):
        with self.c.session_transaction() as s:
            return s.get("csrf", "")

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, csrf=True, **kw):
        headers = {"X-CSRF-Token": self.csrf()} if csrf else {}
        headers.update(kw.pop("headers", {}))
        return self.c.post(url, json=json if json is not None else {}, headers=headers, **kw)

    def delete(self, url):
        return self.c.delete(url, headers={"X-CSRF-Token": self.csrf()})

    def sign_in(self):
        return self.c.get("/auth/google")

    def onboard(self):
        self.sign_in()
        assert self.post("/api/consent", {"items": ["terms", "gmail", "ai", "voice"]}).status_code == 200
        r = self.post("/api/gmail-accounts", {"address": "me@gmail.com", "app_password": "abcd efgh ijkl mnop"})
        assert r.status_code == 200, r.json
        assert self.post("/api/settings", {"voice_lang": "en-US"}).status_code == 200
        return r.json["account"]["id"]


@pytest.fixture()
def client(app):
    return Client(app)
