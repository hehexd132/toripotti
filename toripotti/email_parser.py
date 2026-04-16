"""
Tori.fi hakuvahti -emailin parsija.

KORJAUS: URL-deduplikointi – sama ilmoitus ei enää analysoida kahdesti
vaikka linkki esiintyisi emailissa sekä kuvana että tekstinä.
"""
import re
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TURKU_AREA = {
    "turku", "raisio", "kaarina", "naantali", "lieto",
    "masku", "nousiainen", "mynämäki", "rusko", "paimio",
}

PRICE_RE = re.compile(
    r"(?<!\d)(\d{1,5}(?:[\s\u00a0]\d{3})?)"
    r"(?:[,\.]\d{2})?"
    r"\s*€",
    re.IGNORECASE,
)
FREE_RE  = re.compile(r"\bilmainen\b|\bilmaiseksi\b|\bgratis\b", re.IGNORECASE)
TORI_URL = re.compile(r"https?://(?:www\.)?tori\.fi/[^\s\"'<>\)]+", re.IGNORECASE)
SKIP_RE  = re.compile(
    r"tori\.fi|hakuvahti|peruuta|unsubscribe|tilauksen|ilmoittaudu|"
    r"klikkaa|katso\s+ilmoitus|näytä\s+ilmoitus|avaa\s+ilmoitus",
    re.IGNORECASE,
)


def _normalize_url(url: str) -> str:
    """Poistaa tracking-parametrit URL:sta deduplikointia varten."""
    return url.split("?")[0].split("#")[0].rstrip("/")


class ToriEmailParser:

    def parse(self, email_data: dict) -> dict | None:
        listings = self.parse_all(email_data)
        return listings[0] if listings else None

    def parse_all(self, email_data: dict) -> list[dict]:
        html    = email_data.get("html", "")
        text    = email_data.get("text", "")
        subject = email_data.get("subject", "")

        listings = []

        if html:
            listings = self._from_html_multi(html)

        if not listings and text:
            listings = self._from_text_multi(text)

        if not listings:
            single = self._single_fallback(html or text, subject)
            if single:
                listings = [single]

        count = len(listings)
        logger.info(f"  📋 Emailissa {count} ilmoitusta")
        return listings

    # ── HTML ──────────────────────────────────────────────────────────────

    def _from_html_multi(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")

        # Kerää UNIIKIT tori.fi-ilmoituslinkit
        # Sama ilmoitus voi esiintyä sekä kuvalinkkinä että tekstilinkkinä –
        # deduplikointi estää kaksinkertaisen analysoinnin
        seen_urls  = set()
        tori_links = []

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "tori.fi" in href and re.search(r"/\d{5,}|/ilmoitus", href):
                base = _normalize_url(href)
                if base not in seen_urls:
                    seen_urls.add(base)
                    tori_links.append((a, href))
                # else: sama URL jo listassa, ohitetaan

        if not tori_links:
            return []

        listings = []
        for anchor, url in tori_links:
            listing = self._extract_near_link(anchor, url)
            if listing and listing.get("title"):
                listings.append(listing)
                p = listing["price"]
                price_str = "ILMAINEN" if p == 0 else ("?" if p < 0 else f"{p}€")
                logger.debug(f"    → [{price_str}] {listing['title'][:60]}")

        return listings

    def _extract_near_link(self, anchor, url: str) -> dict | None:
        title = price = location = None
        description = ""

        container = anchor
        for _ in range(6):
            parent = container.parent
            if parent is None or parent.name in ("body", "html", "[document]"):
                break
            container = parent

            block_text = container.get_text(separator="\n", strip=True)
            lines = [
                l.strip() for l in block_text.split("\n")
                if l.strip() and len(l.strip()) > 2
            ]

            if len(lines) < 2:
                continue

            for line in lines:
                ll = line.lower()

                if price is None:
                    if FREE_RE.search(line):
                        price = 0
                    else:
                        m = PRICE_RE.search(line)
                        if m:
                            raw = m.group(1).replace("\u00a0", "").replace(" ", "")
                            try:
                                price = int(raw)
                            except ValueError:
                                pass

                if location is None:
                    for city in TURKU_AREA:
                        if city in ll:
                            location = line
                            break

                if title is None:
                    if not SKIP_RE.search(line) and not re.match(r"^[\d\s€,\.\-–]+$", line):
                        if len(line) > 4 and "@" not in line:
                            title = line
                            continue

                if title and not description and line != title:
                    if not re.match(r"^[\d\s€,\.\-–]+$", line) and "@" not in line:
                        description = line

            if title and price is not None:
                break

        if not title:
            return None

        return {
            "title":       title,
            "price":       price if price is not None else -1,
            "location":    location or "Turku-alue",
            "description": description,
            "url":         url,
        }

    # ── Teksti-email ──────────────────────────────────────────────────────

    def _from_text_multi(self, text: str) -> list[dict]:
        raw_urls = TORI_URL.findall(text)
        segments = re.split(r"https?://(?:www\.)?tori\.fi/[^\s]+", text)

        seen_urls = set()
        listings  = []

        for i, url in enumerate(raw_urls):
            base = _normalize_url(url)
            if base in seen_urls:
                continue
            seen_urls.add(base)

            segment = segments[i] if i < len(segments) else ""
            lines   = [l.strip() for l in segment.split("\n") if l.strip() and len(l.strip()) > 2]

            title = price = location = None

            for line in reversed(lines[-12:]):
                ll = line.lower()

                if price is None:
                    if FREE_RE.search(line):
                        price = 0
                    else:
                        m = PRICE_RE.search(line)
                        if m:
                            raw = m.group(1).replace("\u00a0", "").replace(" ", "")
                            try:
                                price = int(raw)
                            except ValueError:
                                pass

                if location is None:
                    for city in TURKU_AREA:
                        if city in ll:
                            location = line
                            break

                if title is None:
                    if not SKIP_RE.search(line) and not re.match(r"^[\d\s€,\.\-–]+$", line):
                        if len(line) > 4 and "@" not in line:
                            title = line

            if title:
                listings.append({
                    "title":       title,
                    "price":       price if price is not None else -1,
                    "location":    location or "Turku-alue",
                    "description": "",
                    "url":         url,
                })

        return listings

    # ── Fallback ──────────────────────────────────────────────────────────

    def _single_fallback(self, content: str, subject: str) -> dict | None:
        if not content:
            return None
        soup  = BeautifulSoup(content, "html.parser") if "<" in content else None
        text  = soup.get_text(separator="\n") if soup else content
        url_m = TORI_URL.search(content)
        url   = url_m.group(0) if url_m else None

        lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 2]
        title = price = location = None
        desc  = []

        for line in lines:
            ll = line.lower()
            if price is None:
                if FREE_RE.search(line):
                    price = 0
                else:
                    m = PRICE_RE.search(line)
                    if m:
                        raw = m.group(1).replace("\u00a0", "").replace(" ", "")
                        try:
                            price = int(raw)
                        except ValueError:
                            pass
            if location is None:
                for city in TURKU_AREA:
                    if city in ll:
                        location = line; break
            if title is None:
                if not SKIP_RE.search(line) and not re.match(r"^[\d\s€,\.\-–]+$", line):
                    if len(line) > 4 and "@" not in line:
                        title = line; continue
            if title and len(desc) < 3:
                desc.append(line)

        cleaned = re.sub(r"(?i)tori\.fi\s*[-|:]*\s*|hakuvahti\s*[-|:]*\s*", "", subject).strip()
        if not title and len(cleaned) > 3:
            title = cleaned

        if not title:
            return None

        return {
            "title":       title,
            "price":       price if price is not None else -1,
            "location":    location or "Turku-alue",
            "description": " ".join(desc),
            "url":         url,
        }
