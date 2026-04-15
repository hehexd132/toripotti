"""
Tori.fi hakuvahti -emailin parsija.
Käsittelee sekä yksittäiset ilmoitukset että digest-emailit.

KORJAUS: FREE_RE ei enää sisällä 0€ → ei enää väärennä hintoja.
"""
import re
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TURKU_AREA = {
    "turku", "raisio", "kaarina", "naantali", "lieto",
    "masku", "nousiainen", "mynämäki", "rusko", "paimio",
}

# Hinta: "150 €", "1 299€", "1299,00 €"
PRICE_RE = re.compile(
    r"(?<!\d)(\d{1,5}(?:[\s\u00a0]\d{3})?)"   # 1–5 numeroa, mahdollinen tuhaterottaja
    r"(?:[,\.]\d{2})?"                           # desimaalit (valinnainen)
    r"\s*€",
    re.IGNORECASE,
)

# Ilmainen: VAIN sanamuotoja – EI enää "0 €" joka osui virheellisesti "150€":een
FREE_RE = re.compile(r"\bilmainen\b|\bilmaiseksi\b|\bgratis\b", re.IGNORECASE)

# Tori.fi-linkki
TORI_URL = re.compile(r"https?://(?:www\.)?tori\.fi/[^\s\"'<>\)]+", re.IGNORECASE)

# Rivit joita ei käytetä otsikkona
SKIP_RE = re.compile(
    r"tori\.fi|hakuvahti|peruuta|unsubscribe|tilauksen|ilmoittaudu|"
    r"klikkaa|katso\s+ilmoitus|näytä\s+ilmoitus|avaa\s+ilmoitus",
    re.IGNORECASE,
)


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

        logger.info(f"  📋 Emailissa {len(listings)} ilmoitusta")
        return listings

    # ── HTML ──────────────────────────────────────────────────────────────

    def _from_html_multi(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")

        # Kerää kaikki tori.fi-ilmoituslinkit
        tori_links = []
        seen_urls  = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Ilmoituslinkit sisältävät /ilmoitus/ tai pitkän numerosarjan
            if "tori.fi" in href and re.search(r"/\d{5,}|/ilmoitus", href):
                # Poista tracking-parametrit URL:sta vertailua varten
                base = href.split("?")[0]
                if base not in seen_urls:
                    seen_urls.add(base)
                    tori_links.append((a, href))

        if not tori_links:
            return []

        listings = []
        for anchor, url in tori_links:
            listing = self._extract_near_link(anchor, url)
            if listing and listing.get("title"):
                listings.append(listing)
                logger.debug(f"    → [{listing['price']}€] {listing['title'][:60]}")

        return listings

    def _extract_near_link(self, anchor, url: str) -> dict | None:
        """
        Poimii ilmoitustiedot linkin läheltä DOM-puussa.
        Käy ylöspäin vanhempiin kunnes löytää otsikon ja hinnan.
        """
        title = price = location = None
        description = ""

        container = anchor
        for depth in range(6):
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

            # Parsitaan rivit
            for line in lines:
                ll = line.lower()

                # Hinta – tarkista ensin ilmainen-sanat
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

                # Sijainti
                if location is None:
                    for city in TURKU_AREA:
                        if city in ll:
                            location = line
                            break

                # Otsikko – ensimmäinen järkevä rivi
                if title is None:
                    if not SKIP_RE.search(line) and not re.match(r"^[\d\s€,\.\-–]+$", line):
                        if len(line) > 4 and "@" not in line:
                            title = line
                            continue

                # Kuvaus
                if title and not description and line != title:
                    if not re.match(r"^[\d\s€,\.\-–]+$", line) and "@" not in line:
                        description = line

            if title and price is not None:
                break  # Löydettiin riittävästi tietoa

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
        urls     = TORI_URL.findall(text)
        segments = re.split(r"https?://(?:www\.)?tori\.fi/[^\s]+", text)
        listings = []

        for i, url in enumerate(urls):
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
