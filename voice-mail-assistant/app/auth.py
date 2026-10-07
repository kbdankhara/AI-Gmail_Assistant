"""Sign-in with Google. Only name, email and profile picture are read."""
from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import (Blueprint, abort, current_app, g, jsonify, redirect,
                   request, session, url_for)

from .models import User

bp = Blueprint("auth", __name__)
oauth = OAuth()


def init_oauth(app) -> None:
    oauth.init_app(app)
    if app.cfg.google_configured:
        oauth.register(
            name="google",
            client_id=app.cfg.GOOGLE_CLIENT_ID,
            client_secret=app.cfg.GOOGLE_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile", "prompt": "select_account"},
        )


def has_current_consent(user) -> bool:
    version = current_app.cfg.POLICY_VERSION
    return any(c.policy_version == version for c in user.consents)


def next_step(user) -> str:
    """Where a signed-in user belongs: consent, first-time set-up, or the assistant."""
    if not has_current_consent(user):
        return url_for("pages.consent")
    if not user.setup_done:
        return url_for("pages.profile", onboarding=1)
    return url_for("pages.assistant")


def login_required(view):
    """For pages: signed in and consented, otherwise redirect."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("pages.login"))
        if not has_current_consent(g.user):
            return redirect(url_for("pages.consent"))
        return view(*args, **kwargs)
    return wrapped


def api_user(consent: bool = True):
    """For JSON endpoints: signed in (and consented), otherwise 401/403."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return jsonify(error="Please sign in again."), 401
            if consent and not has_current_consent(g.user):
                return jsonify(error="Please review and accept the terms first."), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator


def _sign_in(sub: str, email: str, name: str, picture: str):
    db = current_app.db
    user = db.query(User).filter_by(google_sub=sub).one_or_none()
    if user is None:
        user = User(google_sub=sub, email=email, name=name, picture=picture)
        db.add(user)
    else:
        user.email, user.name, user.picture = email, name or user.name, picture
    db.commit()
    csrf = session.get("csrf")
    session.clear()
    session.permanent = True
    session["uid"] = user.id
    if csrf:
        session["csrf"] = csrf
    return redirect(next_step(user))


@bp.get("/auth/google")
def google_start():
    cfg = current_app.cfg
    if not cfg.google_configured:
        if cfg.DEV_LOGIN or cfg.DEMO_MODE:
            return _sign_in("dev-user", "demo@example.com", "Demo User", "")
        return redirect(url_for("pages.login", error="config"))
    return oauth.google.authorize_redirect(cfg.BASE_URL + url_for("auth.google_callback"))


@bp.get("/auth/google/callback")
def google_callback():
    if not current_app.cfg.google_configured:
        abort(404)
    try:
        token = oauth.google.authorize_access_token()
        info = token.get("userinfo") or {}
        if not info.get("sub") or not info.get("email") or info.get("email_verified") is False:
            raise ValueError("incomplete profile")
    except Exception:
        return redirect(url_for("pages.login", error="google"))
    return _sign_in(info["sub"], info["email"], info.get("name", ""), info.get("picture", ""))


@bp.post("/auth/signout")
def signout():
    session.clear()
    if request.accept_mimetypes.best == "application/json":
        return jsonify(ok=True)
    return redirect(url_for("pages.login"))
