"""Gmail over IMAP and SMTP using an App Password. Nothing fetched here is stored."""
import email
import html
import imaplib
import re
import smtplib
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parseaddr

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"
TIMEOUT = 20
MAX_UNREAD = 25
MAX_BODY_CHARS = 8000


class GmailError(Exception):
    """A problem the user can act on (wrong password, IMAP off, network)."""


@dataclass
class Mail:
    uid: str
    sender_name: str
    sender_email: str
    reply_to: str
    subject: str
    body: str
    message_id: str
    references: str

    def header(self) -> dict:
        return {"uid": self.uid, "from": self.sender_name or self.sender_email,
                "from_email": self.sender_email, "subject": self.subject or "(no subject)"}


def _clean_password(password: str) -> str:
    return re.sub(r"\s+", "", password or "")


def _decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return text


def _body_text(msg) -> str:
    plain, rich = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and rich is None:
            rich = text
    text = plain if plain and plain.strip() else _html_to_text(rich or "")
    # Drop quoted history so summaries cover the new message, not the whole thread.
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith(">") or re.match(r"^On .{10,120} wrote:\s*$", line.strip()):
            break
        kept.append(line.rstrip())
    text = re.sub(r"[ \t]+", " ", "\n".join(kept))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[:MAX_BODY_CHARS]


def _parse(uid: str, raw: bytes) -> Mail:
    msg = email.message_from_bytes(raw)
    name, addr = parseaddr(_decode(msg.get("From")))
    _, reply_to = parseaddr(_decode(msg.get("Reply-To")))
    refs = " ".join(x for x in [(msg.get("References") or "").strip(), (msg.get("Message-ID") or "").strip()] if x)
    return Mail(uid=uid, sender_name=name, sender_email=addr, reply_to=reply_to or addr,
                subject=_decode(msg.get("Subject")), body=_body_text(msg),
                message_id=(msg.get("Message-ID") or "").strip(), references=refs)


class _Imap:
    def __init__(self, address: str, password: str, readonly: bool = True):
        self.address, self.password, self.readonly = address, _clean_password(password), readonly
        self.conn = None

    def __enter__(self):
        try:
            self.conn = imaplib.IMAP4_SSL(IMAP_HOST, timeout=TIMEOUT)
            self.conn.login(self.address, self.password)
            self.conn.select("INBOX", readonly=self.readonly)
        except imaplib.IMAP4.error as exc:
            self._close()
            raise GmailError("Gmail rejected the address or App Password. Check both in Profile settings, "
                             "and make sure 2-Step Verification is on.") from exc
        except OSError as exc:
            self._close()
            raise GmailError("Couldn't reach Gmail. Check the connection and try again.") from exc
        return self.conn

    def __exit__(self, *exc):
        self._close()

    def _close(self):
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:
                pass
            self.conn = None


def test_login(address: str, password: str) -> None:
    with _Imap(address, password):
        pass


def list_unread(address: str, password: str) -> list[dict]:
    """Sender and subject of unread inbox mail, oldest first. Does not mark anything read."""
    with _Imap(address, password) as conn:
        status, data = conn.uid("SEARCH", None, "UNSEEN")
        if status != "OK":
            raise GmailError("Gmail didn't return the unread list. Try again.")
        uids = data[0].split()[-MAX_UNREAD:]
        if not uids:
            return []
        status, data = conn.uid("FETCH", b",".join(uids),
                                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT REPLY-TO MESSAGE-ID REFERENCES)])")
        if status != "OK":
            raise GmailError("Gmail didn't return the unread list. Try again.")
        out = []
        for item in data:
            if not isinstance(item, tuple):
                continue
            match = re.search(rb"UID (\d+)", item[0])
            if match:
                out.append(_parse(match.group(1).decode(), item[1]).header())
        return sorted(out, key=lambda m: int(m["uid"]))


def fetch(address: str, password: str, uid: str) -> Mail:
    if not str(uid).isdigit():
        raise GmailError("That email reference isn't valid.")
    with _Imap(address, password) as conn:
        status, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            raise GmailError("That email is no longer in the inbox.")
        return _parse(str(uid), data[0][1])


def mark_read(address: str, password: str, uid: str) -> None:
    if not str(uid).isdigit():
        raise GmailError("That email reference isn't valid.")
    with _Imap(address, password, readonly=False) as conn:
        status, _ = conn.uid("STORE", str(uid), "+FLAGS", "(\\Seen)")
        if status != "OK":
            raise GmailError("Gmail couldn't mark that email as read.")


def build_reply(address: str, display_name: str, original: Mail, body: str) -> EmailMessage:
    msg = EmailMessage()
    subject = original.subject or ""
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg["From"] = f"{display_name} <{address}>" if display_name else address
    recipients = [a for _, a in getaddresses([original.reply_to]) if a]
    msg["To"] = ", ".join(recipients) or original.sender_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=address.split("@")[-1])
    if original.message_id:
        msg["In-Reply-To"] = original.message_id
        msg["References"] = original.references or original.message_id
    msg.set_content(body.strip() + "\n")
    return msg


def send_reply(address: str, password: str, display_name: str, original: Mail, body: str) -> None:
    msg = build_reply(address, display_name, original, body)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=TIMEOUT) as smtp:
            smtp.login(address, _clean_password(password))
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise GmailError("Gmail rejected the address or App Password when sending.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise GmailError("Gmail couldn't send the reply. Nothing was sent.") from exc


# ---- demo mailbox (DEMO_MODE=1): lets the whole flow run without credentials ----

_DEMO = [
    Mail("101", "Priya Shah", "priya.shah@example.com", "priya.shah@example.com", "Q3 budget review",
         "Hi,\n\nI've attached the revised Q3 numbers after yesterday's call. Marketing is down 8% and "
         "infrastructure is up 12%. Could you confirm the revised numbers before Friday so I can send "
         "them to finance?\n\nThanks,\nPriya", "<demo-101@example.com>", "<demo-101@example.com>"),
    Mail("102", "Daniel Okafor", "daniel@example.com", "daniel@example.com", "Lunch on Thursday?",
         "Hey, are you free for lunch on Thursday around 1? There's a new place near the office I want "
         "to try. Let me know!\n\nDaniel", "<demo-102@example.com>", "<demo-102@example.com>"),
    Mail("103", "Acme Cloud Billing", "billing@example.com", "billing@example.com", "Your invoice for September",
         "Your invoice for September is ready. Amount due: $42.00, charged to the card on file on "
         "October 6. No action is needed.", "<demo-103@example.com>", "<demo-103@example.com>"),
]
DEMO_SUMMARIES = {
    "101": "She asks you to confirm the revised numbers before Friday.",
    "102": "He's asking if you're free for lunch on Thursday around one.",
    "103": "Your September invoice of 42 dollars will be charged on October 6. No action is needed.",
}
_demo_read: dict[int, set[str]] = {}
demo_sent: list[dict] = []


def demo_list_unread(account_id: int) -> list[dict]:
    seen = _demo_read.setdefault(account_id, set())
    return [m.header() for m in _DEMO if m.uid not in seen]


def demo_fetch(uid: str) -> Mail:
    for m in _DEMO:
        if m.uid == str(uid):
            return m
    raise GmailError("That email is no longer in the inbox.")


def demo_mark_read(account_id: int, uid: str) -> None:
    _demo_read.setdefault(account_id, set()).add(str(uid))


def demo_reset() -> None:
    _demo_read.clear()
    demo_sent.clear()
