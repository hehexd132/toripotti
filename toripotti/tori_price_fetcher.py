"""
Tori.fi hintojen haku – oikeat käytettyjen markkinahinnat.

Hakee tori.fi:stä vastaavia myynnissä olevia tuotteita ja laskee
mediaanihinnan → realistinen jälleenmyyntihinta-arvio.

Ei skrapata sivua systemaattisesti – tehdään yksi hakukysely per
ilmoitus, täsmälleen kuten käyttäjä itse hakisi.
"""
import re
import logging
import time

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fi-FI,fi;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PRICE_RE = re.compile(r"(?<!\d)(\d{1,5})\s*€")
TIMEOUT  = 10


class ToriPriceFetcher:
    """Hakee käytetyn tavaran mediaanihinnan tori.fi:stä."""

    # Tori.fi hakuparametrit:
    # w=3 = koko Suomi, st=s = yksityiset, st=k = yritykset
    # cg=0 = kaikki kategoriat
    BASE_URL = "https://www.tori.fi/koko_suomi"

    def fetch(self, query: str, max_price: int | None = None) -> dict | None:
        """
        Palauttaa:
          { median, low, high, count, samples: [int] }
        tai None jos ei tuloksia.
        """
        if not query or len(query) < 3:
            return None

        clean = self._clean_query(query)
        if not clean:
            return None

        params = {
            "q":   clean,
            "cg":  "0",
            "w":   "3",      # koko Suomi
            "st":  ["s", "k", "u"],  # myydään (yksityinen + yritys)
            "ca":  "18",     # Elektroniikka (laaja)
            "o":   "1",
        }

        try:
            resp = requests.get(
                self.BASE_URL,
                params=params,
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            return self._parse(resp.text, max_price)

        except requests.RequestException as exc:
            logger.debug(f"Tori.fi haku epäonnistui ({clean}): {exc}")
            return None
        except Exception as exc:
            logger.debug(f"Tori.fi parsimisvirhe: {exc}")
            return None

    def _parse(self, html: str, max_price: int | None) -> dict | None:
        soup = BeautifulSoup(html, "html.parser")

        prices = []

        # Tori.fi:n hakutulokset ovat li-elementeissä joissa on hinta
        # Yritetään ensin structured data, sitten teksti
        for item in soup.select("li.item_row, div.item_row, article"):
            text = item.get_text()
            for m in PRICE_RE.finditer(text):
                try:
                    val = int(m.group(1))
                    if 5 < val < 50_000:
                        # Suodata pois selvästi liian kalliit (uuden hinta)
                        if max_price and val > max_price * 1.2:
                            continue
                        prices.append(val)
                        break  # yksi hinta per ilmoitus
                except ValueError:
                    pass

        # Fallback: poimi hinnat koko sivun tekstistä
        if not prices:
            all_text = soup.get_text()
            for m in PRICE_RE.finditer(all_text):
                try:
                    val = int(m.group(1))
                    if 5 < val < 50_000:
                        prices.append(val)
                except ValueError:
                    pass

        if not prices:
            return None

        # Suodata outlierit – ota keskimmäinen 60%
        prices_sorted = sorted(prices)
        n      = len(prices_sorted)
        low_i  = n // 5
        high_i = n - n // 5
        trimmed = prices_sorted[low_i:high_i] if n > 4 else prices_sorted

        if not trimmed:
            return None

        median = trimmed[len(trimmed) // 2]
        return {
            "median": median,
            "low":    trimmed[0],
            "high":   trimmed[-1],
            "count":  n,
        }

    @staticmethod
    def _clean_query(name: str) -> str:
        # Poista yleisiä myynti-sanoja
        junk = re.compile(
            r"\b(myydään|hyvä|kunto|toimiva|käytetty|uusi|uutta|vastaava|"
            r"halpa|tarjous|ilmainen|myynnissä|vaihto|ostetaan|etsitään|"
            r"kpl|pari|setti|paketti|erä)\b",
            re.IGNORECASE,
        )
        name = re.sub(r"\([^)]*\)", "", name)
        name = junk.sub("", name).strip()
        # Max 4 sanaa – tori.fi haku toimii paremmin lyhyillä hakutermeillä
        return " ".join(name.split()[:4])
