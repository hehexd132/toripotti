"""
Tori.fi hakuvahti -emailin parsija.

KORJAUKSET:
- Roskasuodatin: footer-tekstit, unsubscribe-linkit, legalese ei läpäise
- parse_all ottaa globaalin seen_urls-setin → sama ilmoitus ei analysoida
  kahdesti vaikka se esiintyisi useammassa hakuvahti-emailissa
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
BULK_RE = re.compile(
    r"\b([2-9]|1[0-9])\s*kpl\b|\bpari\b|\bsetti\b|\bset\b"
    r"|\bpaketti\b|\berä\b|\b(kaksi|kolme|neljä|viisi|kuusi)\b|\b[2-9]x\b",
    re.IGNORECASE,
)

# Otsikot jotka ovat selvästi roskaa / footeria – hylätään heti
JUNK_TITLE_RE = re.compile(
    r"peru\s+s.hk.posti|automaattinen\s+s.hk.posti|ei\s+voi\s+vastata|"
    r"tori\s+on\s+osa|vend.konsernia|tietosuoja|privacy|cookie|"
    r"unsubscribe|tilauksen\s+hallinta|s.hk.postilistalta|"
    r"poista\s+tilaus|hallinnoi\s+tilauksia|ilmoittaudu|rekisteröidy|"
    r"käyttöehdot|asiakaspalvelu@|info@|noreply@|no-reply@",
    re.IGNORECASE,
)


def _normalize_url(url: str) -> str:
    return url.split("?")[0].split("#")[0].rstrip("/")


def _is_junk_title(title: str) -> bool:
    """Palauttaa True jos otsikko on selvästi footer/legalese eikä tuote."""
    if not title:
        return True
    # Liian pitkä → todennäköisesti footer-teksti
    if len(title) > 90:
        return True
    # Sisältää sähköpostiosoitteen
    if "@" in title:
        return True
    # Osuu roskapatterniin
    if JUNK_TITLE_RE.search(title):
        return True
    # Pelkkiä numeroita / välimerkkejä
    if re.match(r"^[\d\s€,\.\-–\|/\\]+$", title):
        return True
    return False


def _detect_bulk(text: str) -> bool:
    return bool(BULK_RE.search(text))


class ToriEmailParser:

    def parse(self, email_data: dict) -> dict | None:
        listings = self.parse_all(email_data)
        return listings[0] if listings else None

    def parse_all(self, email_data: dict, global_seen_urls: set | None = None) -> list[dict]:
        """
        global_seen_urls: jaettu set koko ajon yli → sama URL ei koskaan
        käsitellä kahdesti eri emaileissa. Main.py luo tämän setin ja
        välittää sen jokaiselle parse_all-kutsulle.
        """
        html    = email_data.get("html", "")
        text    = email_data.get("text", "")
        subject = email_data.get("subject", "")

        listings = []
        if html:
            listings = self._from_html_multi(html, global_seen_urls)
        if not listings and text:
            listings = self._from_text_multi(text, global_seen_urls)
        if not listings:
            single = self._single_fallback(html or text, subject, global_seen_urls)
            if single:
                listings = [single]

        # Suodata roskat pois
        clean = [l for l in listings if not _is_junk_title(l.get("title", ""))]
        filtered = len(listings) - len(clean)
        if filtered:
            logger.debug(f"  🗑️  Suodatettiin {filtered} roskailmoitusta")

        logger.info(f"  📋 Emailissa {len(clean)} ilmoitusta (uniikkia tässä ajossa)")
        return clean

    # ── HTML ──────────────────────────────────────────────────────────────

    def _from_html_multi(self, html: str, global_seen: set | None) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        local_seen = set()

        tori_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "tori.fi" in href and re.search(r"/\d{5,}|/ilmoitus", href):
                base = _normalize_url(href)
                # Tarkista sekä lokaalit että globaalit duplikaatit
                if base not in local_seen and (global_seen is None or base not in global_seen):
                    local_seen.add(base)
                    if global_seen is not None:
                        global_seen.add(base)
                    tori_links.append((a, href))

        listings = []
        for anchor, url in tori_links:
            listing = self._extract_near_link(anchor, url)
            if listing and listing.get("title"):
                listings.append(listing)
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
            lines = [l.strip() for l in block_text.split("\n") if l.strip() and len(l.strip()) > 2]
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
                                price = int(m.group(1).replace("\u00a0", "").replace(" ", ""))
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
                if title and not description and line != title:
                    if not re.match(r"^[\d\s€,\.\-–]+$", line) and "@" not in line:
                        description = line
            if title and price is not None:
                break

        if not title or _is_junk_title(title):
            return None
        combined = f"{title} {description}"
        return {
            "title": title, "price": price if price is not None else -1,
            "location": location or "Turku-alue", "description": description,
            "url": url, "is_bulk": _detect_bulk(combined),
        }

    # ── Teksti-email ──────────────────────────────────────────────────────

    def _from_text_multi(self, text: str, global_seen: set | None) -> list[dict]:
        raw_urls  = TORI_URL.findall(text)
        segments  = re.split(r"https?://(?:www\.)?tori\.fi/[^\s]+", text)
        local_seen = set()
        listings  = []

        for i, url in enumerate(raw_urls):
            base = _normalize_url(url)
            if base in local_seen or (global_seen is not None and base in global_seen):
                continue
            local_seen.add(base)
            if global_seen is not None:
                global_seen.add(base)

            segment = segments[i] if i < len(segments) else ""
            lines   = [l.strip() for l in segment.split("\n") if l.strip() and len(l.strip()) > 2]
            title = price = location = None

            for line in reversed(lines[-12:]):
                ll = line.lower()
                if price is None:
                    if FREE_RE.search(line): price = 0
                    else:
                        m = PRICE_RE.search(line)
                        if m:
                            try: price = int(m.group(1).replace("\u00a0", "").replace(" ", ""))
                            except ValueError: pass
                if location is None:
                    for city in TURKU_AREA:
                        if city in ll: location = line; break
                if title is None:
                    if not SKIP_RE.search(line) and not re.match(r"^[\d\s€,\.\-–]+$", line):
                        if len(line) > 4 and "@" not in line: title = line

            if title and not _is_junk_title(title):
                listings.append({
                    "title": title, "price": price if price is not None else -1,
                    "location": location or "Turku-alue", "description": "",
                    "url": url, "is_bulk": _detect_bulk(title),
                })
        return listings

    # ── Fallback ──────────────────────────────────────────────────────────

    def _single_fallback(self, content: str, subject: str, global_seen: set | None) -> dict | None:
        if not content: return None
        soup  = BeautifulSoup(content, "html.parser") if "<" in content else None
        text  = soup.get_text(separator="\n") if soup else content
        url_m = TORI_URL.search(content)
        url   = url_m.group(0) if url_m else None

        if url:
            base = _normalize_url(url)
            if global_seen is not None and base in global_seen:
                return None
            if global_seen is not None:
                global_seen.add(base)

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
                        try: price = int(m.group(1).replace("\u00a0", "").replace(" ", ""))
                        except ValueError: pass
            if location is None:
                for city in TURKU_AREA:
                    if city in ll: location = line; break
            if title is None:
                if not SKIP_RE.search(line) and not re.match(r"^[\d\s€,\.\-–]+$", line):
                    if len(line) > 4 and "@" not in line: title = line; continue
            if title and len(desc) < 3: desc.append(line)

        cleaned = re.sub(r"(?i)tori\.fi\s*[-|:]*\s*|hakuvahti\s*[-|:]*\s*", "", subject).strip()
        if not title and len(cleaned) > 3: title = cleaned
        if not title or _is_junk_title(title): return None
        combined = f"{title} {' '.join(desc)}"
        return {
            "title": title, "price": price if price is not None else -1,
            "location": location or "Turku-alue", "description": " ".join(desc),
            "url": url, "is_bulk": _detect_bulk(combined),
        }
