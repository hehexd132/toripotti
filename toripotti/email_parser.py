"""
Tori.fi hakuvahti -emailin parsija.

Tori.fi lähettää HTML-emaileja joissa on:
- Ilmoituksen otsikko
- Hinta (tai "Ilmainen")
- Sijainti
- Lyhyt kuvaus
- Linkki ilmoitukseen
"""
import re
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Turku-alueen paikkakunnat (pienillä kirjaimilla vertailua varten)
TURKU_AREA = {
    "turku", "raisio", "kaarina", "naantali", "lieto",
    "masku", "nousiainen", "mynämäki", "rusko", "paimio",
}

# Rivit jotka sisältävät nämä sanat ohitetaan
SKIP_PATTERNS = re.compile(
    r"tori\.fi|hakuvahti|peruuta|unsubscribe|klikkaa|katso ilmoitus|"
    r"näytä ilmoitus|avaa ilmoitus|@|\bhttp\b",
    re.IGNORECASE,
)

PRICE_RE   = re.compile(r"(\d[\d\s]{0,6})\s*€")
FREE_RE    = re.compile(r"ilmainen|ilmaiseksi|0\s*€", re.IGNORECASE)
TORI_URL   = re.compile(r"https?://(?:www\.)?tori\.fi/[^\s\"'<>]+", re.IGNORECASE)


class ToriEmailParser:

    def parse(self, email_data: dict) -> dict | None:
        """
        Palauttaa:
          { title, price (int, 0 = ilmainen, -1 = tuntematon),
            location, description, url }
        tai None jos parsiminen epäonnistui täysin.
        """
        html    = email_data.get("html", "")
        text    = email_data.get("text", "")
        subject = email_data.get("subject", "")

        if html:
            result = self._from_html(html, subject)
        else:
            result = self._from_text(text, subject)

        # Fallback: käytä emailin aihetta otsikkona
        if result and not result.get("title"):
            result["title"] = self._title_from_subject(subject)

        if result and result.get("title"):
            return result

        logger.warning(f"Parsiminen epäonnistui kokonaan. Subject: {subject[:80]}")
        return None

    # ── HTML ──────────────────────────────────────────────────────────────
    def _from_html(self, html: str, subject: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n", strip=True)

        # Etsi tori.fi-linkki
        url = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "tori.fi" in href and (
                "/ilmoitus" in href or "/item" in href or re.search(r"/\d{7,}", href)
            ):
                url = href
                break
        if not url:
            m = TORI_URL.search(html)
            url = m.group(0) if m else None

        lines = self._clean_lines(text)
        return self._extract(lines, url, subject)

    # ── Teksti ────────────────────────────────────────────────────────────
    def _from_text(self, text: str, subject: str) -> dict:
        m   = TORI_URL.search(text)
        url = m.group(0) if m else None
        lines = self._clean_lines(text)
        return self._extract(lines, url, subject)

    # ── Ydinlogiikka ──────────────────────────────────────────────────────
    def _extract(self, lines: list[str], url: str | None, subject: str) -> dict:
        title       = None
        price       = None
        location    = None
        desc_parts  = []

        for line in lines:
            ll = line.lower()

            # Hinta
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

            # Sijainti
            if location is None:
                for city in TURKU_AREA:
                    if city in ll:
                        location = line
                        break

            # Ohita metadatarivit otsikkoa etsittäessä
            if SKIP_PATTERNS.search(line):
                continue
            if re.match(r"^https?://", line):
                continue

            # Otsikko: ensimmäinen järkevä tekstrivi
            if title is None and len(line) > 4:
                # Ohita pelkät numerot/hinnat
                if not re.match(r"^[\d\s€,\.]+$", line):
                    title = line
                    continue

            # Kuvaus: muutama rivi otsikon jälkeen
            if title and len(desc_parts) < 5:
                if line not in (title, location or ""):
                    if not re.match(r"^[\d\s€,\.]+$", line):
                        desc_parts.append(line)

        # Hinta ja otsikko emailin aiheesta jos ei muualta
        if price is None:
            m = PRICE_RE.search(subject)
            if m:
                try:
                    price = int(m.group(1).replace(" ", ""))
                except ValueError:
                    pass
            elif FREE_RE.search(subject):
                price = 0

        if not title:
            title = self._title_from_subject(subject)

        return {
            "title":       title,
            "price":       price if price is not None else -1,
            "location":    location or "Turku-alue",
            "description": " ".join(desc_parts),
            "url":         url,
        }

    # ── Apumetodit ────────────────────────────────────────────────────────
    @staticmethod
    def _clean_lines(text: str) -> list[str]:
        return [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 2]

    @staticmethod
    def _title_from_subject(subject: str) -> str | None:
        """Poimii tuotenimen emailin aiheesta."""
        patterns = [
            r'(?:hakuvahti|osuma|ilmoitus|alert)[:\s\-–]+(.+)',
            r'"([^"]+)"',
            r"'([^']+)'",
        ]
        for pat in patterns:
            m = re.search(pat, subject, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        # Siivoa tori.fi-prefixit
        cleaned = re.sub(r"(?i)tori\.fi\s*[-|:]*\s*|hakuvahti\s*[-|:]*\s*", "", subject).strip()
        return cleaned if len(cleaned) > 3 else None
