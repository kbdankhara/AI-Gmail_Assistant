"""Voice Mail Assistant: application factory."""
import hmac
import secrets

from cryptography.fernet import Fernet
from flask import Flask, g, jsonify, render_template, request, session

from . import crypto
from .config import Config
from .models import User, make_session


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    cfg = Config()
    for key, value in (overrides or {}).items():
        setattr(cfg, key, value)
    app.cfg = cfg

    insecure_dev = cfg.DEV_LOGIN or cfg.DEMO_MODE
    if not cfg.SECRET_KEY or not cfg.ENCRYPTION_KEY:
        if not insecure_dev:
            raise RuntimeError("Set SECRET_KEY and ENCRYPTION_KEY (see .env.example). "
                               "Generate ENCRYPTION_KEY with: python manage.py generate-key")
        # Development only: throwaway keys, so saved secrets do not survive a restart.
        cfg.SECRET_KEY = cfg.SECRET_KEY or secrets.token_hex(32)
        cfg.ENCRYPTION_KEY = cfg.ENCRYPTION_KEY or Fernet.generate_key().decode()

    app.config.update(
        SECRET_KEY=cfg.SECRET_KEY,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.BASE_URL.startswith("https://"),
        PERMANENT_SESSION_LIFETIME=cfg.PERMANENT_SESSION_LIFETIME,
        MAX_CONTENT_LENGTH=cfg.MAX_CONTENT_LENGTH,
    )
    crypto.init(cfg.ENCRYPTION_KEY)
    app.db = make_session(cfg.DATABASE_URL)

    @app.teardown_appcontext
    def _remove_session(exc=None):
        app.db.remove()

    @app.before_request
    def _load_user():
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = app.db.get(User, uid)
            if g.user is None:
                session.clear()
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)

    @app.before_request
    def _csrf():
        if request.method in ("GET", "HEAD", "OPTIONS") or request.path.startswith("/webhooks/"):
            return None
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
        if not hmac.compare_digest(sent, session.get("csrf", "")):
            return jsonify(error="Your session expired. Reload the page and try again."), 400
        return None

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "microphone=(self)")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def _inject():
        return {"cfg": cfg, "user": g.get("user"), "csrf_token": session.get("csrf", "")}

    from .api import bp as api_bp
    from .auth import bp as auth_bp, init_oauth
    from .pages import bp as pages_bp

    init_oauth(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)

    def _error_page(code, title, text):
        if request.path.startswith("/api/") or request.path.startswith("/webhooks/"):
            return jsonify(error=title), code
        return render_template("error.html", code=code, title=title, text=text), code

    app.register_error_handler(403, lambda e: _error_page(403, "Access denied", "You don't have permission to see this page."))
    app.register_error_handler(404, lambda e: _error_page(404, "Page not found", "That page doesn't exist or has moved."))
    app.register_error_handler(413, lambda e: (jsonify(error="That request is too large."), 413))
    app.register_error_handler(500, lambda e: _error_page(500, "Something went wrong", "It's us, not you. Please try again in a moment."))

    return app
