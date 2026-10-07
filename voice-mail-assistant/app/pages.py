"""HTML pages."""
from flask import (Blueprint, current_app, g, redirect, render_template,
                   request, url_for)

from . import billing
from .auth import has_current_consent, login_required, next_step
from .config import CONSENT_ITEMS, PROVIDERS, VOICE_LANGS

bp = Blueprint("pages", __name__)


def app_state(user) -> dict:
    """Everything the signed-in screens need. Secrets are never included, only whether one is saved."""
    cfg = current_app.cfg
    saved = {k.provider for k in user.ai_keys}
    provider = user.ai_provider if user.ai_provider in PROVIDERS else "anthropic"
    gmail_ready = any(a.enc_password for a in user.gmail_accounts)
    return {
        "user": {"name": user.name, "first_name": user.first_name, "email": user.email},
        "gmail_accounts": [{"id": a.id, "address": a.address, "saved": bool(a.enc_password)}
                           for a in user.gmail_accounts],
        "ai": {"provider": provider, "model": user.ai_model or PROVIDERS[provider]["model"],
               "saved": sorted(saved), "short": PROVIDERS[provider]["short"]},
        "providers": PROVIDERS,
        "voice_lang": user.voice_lang if user.voice_lang in VOICE_LANGS else "en-US",
        "setup": {"gmail": gmail_ready, "ai": provider in saved},
        "billing": billing.summary(cfg, user),
        "demo": cfg.DEMO_MODE,
        "trial_days": cfg.TRIAL_DAYS,
        "price": cfg.PLAN_PRICE_DISPLAY,
    }


@bp.get("/")
def landing():
    return render_template("landing.html")


@bp.get("/login")
def login():
    if g.user is not None:
        return redirect(next_step(g.user))
    error = request.args.get("error")
    mode = "create" if request.args.get("mode") == "create" else "signin"
    return render_template("login.html", error=error, mode=mode)


@bp.get("/consent")
def consent():
    if g.user is None:
        return redirect(url_for("pages.login"))
    if has_current_consent(g.user):
        return redirect(next_step(g.user))
    return render_template("consent.html", items=CONSENT_ITEMS)


@bp.get("/assistant")
@login_required
def assistant():
    if not g.user.setup_done and not g.user.gmail_accounts:
        return redirect(url_for("pages.profile", onboarding=1))
    return render_template("assistant.html", state=app_state(g.user))


@bp.get("/profile")
@login_required
def profile():
    onboarding = request.args.get("onboarding") == "1" and not g.user.setup_done
    return render_template("profile.html", state=app_state(g.user), onboarding=onboarding,
                           voice_langs=VOICE_LANGS, consents=g.user.consents)


@bp.get("/payment")
@login_required
def payment():
    return render_template("payment.html", state=app_state(g.user))


@bp.get("/help")
def help_center():
    return render_template("help.html", providers=PROVIDERS)


@bp.get("/privacy")
def privacy():
    return render_template("privacy.html")


@bp.get("/terms")
def terms():
    return render_template("terms.html")
