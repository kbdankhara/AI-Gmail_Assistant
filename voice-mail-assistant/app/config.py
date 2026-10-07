"""Configuration, read from environment variables (see .env.example)."""
import os

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Config:
    APP_NAME = "Voice Mail Assistant"

    # Bump this when the Terms or Privacy Policy change; users are asked to consent again.
    POLICY_VERSION = os.environ.get("POLICY_VERSION", "2026-10-01")

    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")
    DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///vma.db")
    BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")

    # Demo mode: sample inbox and canned AI, no Gmail or AI credentials needed.
    DEMO_MODE = _bool("DEMO_MODE")
    # Lets you sign in without Google while developing. Never enable in production.
    DEV_LOGIN = _bool("DEV_LOGIN")

    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
    RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
    RAZORPAY_PLAN_ID = os.environ.get("RAZORPAY_PLAN_ID", "")

    PLAN_PRICE_DISPLAY = os.environ.get("PLAN_PRICE_DISPLAY", "$5")
    PLAN_AMOUNT_MINOR = int(os.environ.get("PLAN_AMOUNT_MINOR", "500"))
    PLAN_CURRENCY = os.environ.get("PLAN_CURRENCY", "USD")
    TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "30"))

    SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "support@example.com")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = BASE_URL.startswith("https://")
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30
    MAX_CONTENT_LENGTH = 256 * 1024

    @property
    def google_configured(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)

    @property
    def razorpay_configured(self) -> bool:
        return bool(self.RAZORPAY_KEY_ID and self.RAZORPAY_KEY_SECRET and self.RAZORPAY_PLAN_ID)


PROVIDERS = {
    "anthropic": {"label": "Anthropic Claude (Sonnet)", "short": "Claude Sonnet", "model": "claude-sonnet-5-5", "key_hint": "sk-ant-···"},
    "gemini": {"label": "Google Gemini", "short": "Gemini", "model": "gemini-2.5-flash", "key_hint": "AIza···"},
    "openai": {"label": "OpenAI", "short": "OpenAI", "model": "gpt-4o-mini", "key_hint": "sk-···"},
    "groq": {"label": "Groq", "short": "Groq", "model": "llama-3.3-70b-versatile", "key_hint": "gsk_···"},
}

VOICE_LANGS = {"en-US": "English (US)", "en-GB": "English (UK)", "en-IN": "English (India)"}

CONSENT_ITEMS = [
    {"key": "terms", "title": "Terms and Privacy Policy",
     "text": "I've read and agree to the Terms of Service and Privacy Policy."},
    {"key": "gmail", "title": "Access to my Gmail",
     "text": "Read unread mail, mark it as read, and send replies I approve, using the App Password I provide."},
    {"key": "ai", "title": "AI processing of my email content",
     "text": "Sender, subject and message text go to the AI provider I choose, using my own key."},
    {"key": "voice", "title": "Microphone and speaker",
     "text": "Allow use while the assistant is on. Speech recognition happens in my browser."},
]
