"""
Tori.fi hakuvahti -emailin parsija.
Osaa käsitellä sekä yksittäiset ilmoitukset että
päivittäiset koosteemailit (useita ilmoituksia per email).
"""
import re
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TURKU_AREA = {
    "turku", "raisio", "kaarina", "naantali", "lieto",
    "masku", "nousiainen", "mynämäki", "rusko", "paimio",
}

PRICE_RE = re.compile(r"(\d[\d\s]{0,6})\s*€")
FREE_RE  = re.compile(r"ilmainen|ilmaiseksi|0\s*€", re.IGNORECASE)
TORI_URL = re.compile(r"https?://(?:www\.)?tori\.fi/[^\s\"'<>]+", re.IGNORECASE)
SKIP_RE  = re.compile(
    r"tori\.fi|hakuvahti|peruuta|unsubscribe|klikkaa|katso ilmoitus|"
    r"näytä ilmoitus|avaa ilmoitus|@|\bhttp\b",
    re.IGNORECASE,
)


class ToriEmailParser:

    def parse(self, email_data: dict) -> dict | None:
        """Palauttaa ensimmäisen ilmoituksen – yhteensopivuusmetodi."""
        listings = self.parse_all(email_data)
        return listings[0] if listings else None

    def parse_all(self, email_data: dict) -> list[dict]:
        """Parsii KAIKKI ilmoitukset yhdestä emailista."""
        html    = email_data.get("html", "")
        text    = email_data.get("text", "")
        subject = email_data.get("subject", "")

        listings = []

        if html:
            listings = self._from_html_multi(html, subject)

        if not listings:
            listings = self._from_text_multi(text, subject)

        if not listings:
            single = self._single_fallback(html or text, subject)
            if single:
                listings = [single]

        logger.info(f"  📋 Emailissa {len(listings)} ilmoitusta")
        return listings

    # ── HTML ──────────────────────────────────────────────────────────────

    def _from_html_multi(self, html: str, subject: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")

        tori_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "tori.fi" in href and (
                "/ilmoitus" in href or "/item" in href or re.search(r"/\d{6,}", href)
            ):
                tori_links.append((a, href))

        listings = []
        for anchor, url in tori_links:
            listing = self._extract_near_link(anchor, url)
            if listing and listing.get("title"):
                listings.append(listing)

        return listings

    def _extract_near_link(self, anchor, url: str) -> dict:
        title = price = location = None
        description = ""

        container = anchor
        for _ in range(5):
            parent = container.parent
            if parent is None or parent.name in ("body", "html", "[document]"):
                break
            container = parent
            text_block = container.get_text(separator="\n", strip=True)
            lines = [l.strip() for l in text_block.split("\n") if l.strip() and len(l.strip()) > 2]

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
                            try:
                                price = int(m.group(1).replace(" ", ""))
                            except ValueError:
                                pass

                if location is None:
                    for city in TURKU_AREA:
                        if city in ll:
                            location = line
                            break

                if title is None:
                    if not SKIP_RE.search(line) and not re.match(r"^[\d\s€,\.]+$", line):
                        if len(line) > 4:
                            title = line
                            continue

                if title and not description and line != title:
                    if not re.match(r"^[\d\s€,\.]+$", line):
                        description = line

            if title:
                break

        return {
            "title":       title,
            "price":       price if price is not None else -1,
            "location":    location or "Turku-alue",
            "description": description,
            "url":         url,
        }

    # ── Teksti ────────────────────────────────────────────────────────────

    def _from_text_multi(self, text: str, subject: str) -> list[dict]:
        if not text:
            return []

        urls     = TORI_URL.findall(text)
        segments = re.split(r"https?://(?:www\.)?tori\.fi/[^\s]+", text)
        listings = []

        for i, url in enumerate(urls):
            segment = segments[i] if i < len(segments) else ""
            lines   = [l.strip() for l in segment.split("\n") if l.strip() and len(l.strip()) > 2]

            title = price = location = None

            for line in reversed(lines[-10:]):
                ll = line.lower()

                if price is None:
                    if FREE_RE.search(line):
                        price = 0
                    else:
                        m = PRICE_RE.search(line)
                        if m:
                            try:
                                price = int(m.group(1).replace(" ", ""))
                            except ValueError:
                                pass

                if location is None:
                    for city in TURKU_AREA:
                        if city in ll:
                            location = line
                            break

                if title is None:
                    if not SKIP_RE.search(line) and not re.match(r"^[\d\s€,\.]+$", line):
                        if len(line) > 4:
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

        soup = BeautifulSoup(content, "html.parser") if "<" in content else None
        text = soup.get_text(separator="\n") if soup else content

        url_m = TORI_URL.search(content)
        url   = url_m.group(0) if url_m else None

        lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 2]
        title = price = location = None
        desc  = []

        for line in lines:
            ll = line.lower()
            if price is None:
                if FREE_RE.search(line): price = 0
                else:
                    m = PRICE_RE.search(line)
                    if m:
                        try: price = int(m.group(1).replace(" ", ""))
                        except ValueError: pass
            if location is None:
                for city in TURKU_AREA:
                    if city in ll: location = line; break
            if title is None:
                if not SKIP_RE.search(line) and not re.match(r"^[\d\s€,\.]+$", line):
                    if len(line) > 4: title = line; continue
            if title and len(desc) < 3:
                desc.append(line)

        if not title:
            title = re.sub(r"(?i)tori\.fi\s*[-|:]*\s*|hakuvahti\s*[-|:]*\s*", "", subject).strip()

        if not title or len(title) < 3:
            return None

        return {
            "title":       title,
            "price":       price if price is not None else -1,
            "location":    location or "Turku-alue",
            "description": " ".join(desc),
            "url":         url,
        }
