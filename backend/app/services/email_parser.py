"""
Parses a raw Gmail message payload into a normalized ParsedEmail:
headers, plain text, html-stripped-to-text, forwarded/quoted sections,
and every candidate email address found anywhere in the message.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from app.utils.email_utils import extract_all_emails, extract_forwarded_from_blocks

QUOTE_MARKERS = [
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^-{2,}\s*Forwarded [Mm]essage\s*-{2,}", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^On .{0,80} wrote:\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^From:\s*.+$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Begin forwarded message:", re.IGNORECASE | re.MULTILINE),
]


class _HTMLTextExtractor(HTMLParser):
    """Minimal, dependency-free HTML -> text converter, good enough for email bodies."""

    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1
        if tag in ("br", "p", "div", "tr", "li"):
            self._chunks.append("\n")
        if tag == "a":
            # A mailto link's visible anchor text can be a display name rather
            # than the address itself (e.g. <a href="mailto:x@y.com">Naveen</a>),
            # so the address must be pulled from the href, not just handle_data.
            for name, value in attrs:
                if name == "href" and value and value.lower().startswith("mailto:"):
                    address = value[len("mailto:"):].split("?", 1)[0]
                    if address:
                        self._chunks.append(f" {address} ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in ("p", "div", "tr"):
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        text = "".join(self._chunks)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_text(html: str) -> str:
    if not html:
        return ""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return parser.get_text()


def split_quoted_sections(text: str) -> list[str]:
    """
    Split a plain-text email body into [top-level reply, quoted/forwarded block 1, ...]
    using common quote/forward markers. First element is the "new" content the sender
    actually wrote; remaining elements are prior quoted/forwarded messages.
    """
    if not text:
        return [""]

    earliest_idx = len(text)
    for marker in QUOTE_MARKERS:
        m = marker.search(text)
        if m and m.start() < earliest_idx:
            earliest_idx = m.start()

    if earliest_idx == len(text):
        return [text]

    top = text[:earliest_idx].strip()
    quoted = text[earliest_idx:].strip()
    return [top, quoted]


def strip_quote_prefixes(text: str) -> str:
    """Remove leading '>' quote markers from lines (common in plain-text quoted replies)."""
    lines = text.splitlines()
    cleaned = [re.sub(r"^\s*>+\s?", "", line) for line in lines]
    return "\n".join(cleaned)


@dataclass
class ParsedEmail:
    gmail_message_id: str
    thread_id: str
    from_header: str
    from_email: str
    to_header: str
    to_email: str
    subject: str
    message_id_header: str | None
    in_reply_to_header: str | None
    references_header: str | None
    plain_text: str
    html_text: str
    full_text: str  # plain_text + html_text combined, for extraction purposes
    new_content: str  # the top-level (non-quoted) portion of the message
    quoted_content: str  # everything below the first quote/forward marker
    forwarded_from_blocks: list[tuple[str, str]] = field(default_factory=list)
    all_emails: list[str] = field(default_factory=list)

    def candidate_recruiter_emails(self, exclude: set[str]) -> list[str]:
        return [e for e in self.all_emails if e not in exclude]


def _decode_b64(data: str) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8") + b"==").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _walk_parts(payload: dict, plain_chunks: list[str], html_chunks: list[str]) -> None:
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")

    if data:
        decoded = _decode_b64(data)
        if mime_type == "text/plain":
            plain_chunks.append(decoded)
        elif mime_type == "text/html":
            html_chunks.append(decoded)

    for part in payload.get("parts", []) or []:
        _walk_parts(part, plain_chunks, html_chunks)


def _header(headers: list[dict], name: str) -> str | None:
    name = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name:
            return h.get("value")
    return None


def parse_gmail_message(raw_message: dict) -> ParsedEmail:
    """
    raw_message: the dict returned by Gmail API `users.messages.get(format="full")`.
    """
    payload = raw_message.get("payload", {})
    headers = payload.get("headers", []) or []

    plain_chunks: list[str] = []
    html_chunks: list[str] = []
    _walk_parts(payload, plain_chunks, html_chunks)

    plain_text = "\n".join(strip_quote_prefixes(c) for c in plain_chunks if c)
    html_raw = "\n".join(html_chunks)
    html_text = html_to_text(html_raw)

    full_text = "\n\n".join([t for t in (plain_text, html_text) if t])

    sections = split_quoted_sections(plain_text or html_text)
    new_content = sections[0] if sections else ""
    quoted_content = "\n\n".join(sections[1:]) if len(sections) > 1 else ""

    from_header = _header(headers, "From") or ""
    from_emails = extract_all_emails(from_header)
    from_email = from_emails[0] if from_emails else ""

    to_header = _header(headers, "To") or ""
    to_emails = extract_all_emails(to_header)
    to_email = to_emails[0] if to_emails else ""

    forwarded_blocks = extract_forwarded_from_blocks(full_text)

    all_emails = extract_all_emails(full_text)
    # also include forwarded from-block addresses (may appear w/ different formatting)
    for _, e in forwarded_blocks:
        if e not in all_emails:
            all_emails.append(e)

    return ParsedEmail(
        gmail_message_id=raw_message.get("id", ""),
        thread_id=raw_message.get("threadId", ""),
        from_header=from_header,
        from_email=from_email,
        to_header=to_header,
        to_email=to_email,
        subject=_header(headers, "Subject") or "",
        message_id_header=_header(headers, "Message-ID"),
        in_reply_to_header=_header(headers, "In-Reply-To"),
        references_header=_header(headers, "References"),
        plain_text=plain_text,
        html_text=html_text,
        full_text=full_text,
        new_content=new_content,
        quoted_content=quoted_content,
        forwarded_from_blocks=forwarded_blocks,
        all_emails=all_emails,
    )
