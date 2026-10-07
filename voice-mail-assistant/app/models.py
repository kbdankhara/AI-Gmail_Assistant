"""Database models. Email content is never stored; only account settings are."""
from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import (DeclarativeBase, Mapped, mapped_column,
                            relationship, scoped_session, sessionmaker)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    picture: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ai_provider: Mapped[str] = mapped_column(String(32), default="anthropic")
    ai_model: Mapped[str] = mapped_column(String(128), default="")
    voice_lang: Mapped[str] = mapped_column(String(16), default="en-US")
    setup_done: Mapped[bool] = mapped_column(Boolean, default=False)

    # Billing
    sub_status: Mapped[str] = mapped_column(String(32), default="none")
    sub_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    trial_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    access_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    consents = relationship("Consent", cascade="all, delete-orphan", order_by="Consent.accepted_at")
    gmail_accounts = relationship("GmailAccount", cascade="all, delete-orphan", order_by="GmailAccount.id")
    ai_keys = relationship("AIKey", cascade="all, delete-orphan")

    @property
    def first_name(self) -> str:
        return (self.name or self.email).split(" ")[0].split("@")[0] or "there"

    @property
    def initial(self) -> str:
        return (self.first_name[:1] or "?").upper()


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    policy_version: Mapped[str] = mapped_column(String(32))
    items: Mapped[str] = mapped_column(String(255))
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")


class GmailAccount(Base):
    __tablename__ = "gmail_accounts"
    __table_args__ = (UniqueConstraint("user_id", "address"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(String(320))
    enc_password: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AIKey(Base):
    __tablename__ = "ai_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    enc_key: Mapped[str] = mapped_column(Text)


def make_session(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return scoped_session(sessionmaker(bind=engine, expire_on_commit=False))
