"""Quelltexte als Text behandeln: HTML in Klartext umwandeln, Links prüfen, Normalisierung."""
import html
import ipaddress
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urlsplit

_BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol", "table"}
_SKIP_TAGS = {"script", "style"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS and tag != "br":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """Wandelt geliefertes HTML in Klartext um. Es wird kein Markup übernommen."""
    if not value:
        return ""
    value = value.strip()
    # Der FDB-Export liefert CDATA-Blöcke als maskierten Text mit.
    if value.startswith("<![CDATA["):
        value = value[len("<![CDATA["):]
        if value.endswith("]]>"):
            value = value[:-3]
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", value)
        text = html.unescape(text)
    return clean_text(text)


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fold(text: str | None) -> str:
    """Einheitliche Kleinschreibung (ß → ss) für Volltextindex und Suchbegriffe.
    Umlaute bleiben erhalten, damit z. B. „Straßenbau“ nicht auf „Straßenbäume“ passt."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", text).casefold()


def validate_url(url: str | None) -> str | None:
    """Gibt die URL zurück, wenn sie ein plausibler öffentlicher http(s)-Link ist, sonst None."""
    if not url:
        return None
    url = url.strip()
    if len(url) > 2000 or any(c.isspace() for c in url):
        return None
    if url.lower().startswith("www."):
        url = "https://" + url
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    if parts.username or parts.password:
        return None
    host = parts.hostname or ""
    if not host or "." not in host:
        return None
    if host in ("localhost",) or host.endswith(".local"):
        return None
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return None
    except ValueError:
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            # IDN-Hosts werden über punycode akzeptiert
            try:
                host.encode("idna")
            except UnicodeError:
                return None
    return url


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9])")


def sentences(text: str) -> list[str]:
    out = []
    for para in text.split("\n"):
        para = para.strip(" •")
        if para:
            out.extend(s.strip() for s in _SENT_SPLIT.split(para) if s.strip())
    return out


def short(text: str | None, limit: int = 320) -> str:
    """Kürzt an Wortgrenzen, ohne Inhalte umzuformulieren."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") + " …"
