"""
Hintojen haku suomalaisista verkkokaupoista.

Ensisijaisesti: Verkkokauppa.com (luotettavin API)
Varmuuskopio: Gigantti, Power
"""
import logging
import re
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
    "Accept-Language": "fi-FI,fi;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TIMEOUT = 12  # sekuntia


class PriceFetcher:

    def search_price(self, product_name: str) -> dict | None:
        """
        Hakee uuden tuotteen hinnan.
        Palauttaa {'price': int, 'store': str, 'product_name': str} tai None.
        """
        if not product_name or len(product_name) < 3:
            return None

        # Lyhennetty hakutermi – poista ylimääräiset sanat
        query = self._clean_query(product_name)

        prices = []
        for store_fn, store_name in [
            (self._verkkokauppa, "Verkkokauppa.com"),
            (self._gigantti,     "Gigantti"),
            (self._power,        "Power"),
        ]:
            try:
                result = store_fn(query)
                if result:
                    prices.append(result)
                    logger.debug(f"  {store_name}: {result['price']}€")
                time.sleep(0.8)  # Kohteliaisuusviive serverille
            except requests.RequestException as exc:
                logger.debug(f"  {store_name} haku epäonnistui: {exc}")
            except Exception as exc:
                logger.debug(f"  {store_name} tuntematon virhe: {exc}")

        if not prices:
            return None

        # Palauta halvin löytynyt hinta
        return min(prices, key=lambda x: x["price"])

    # ── Kaupat ────────────────────────────────────────────────────────────

    def _verkkokauppa(self, query: str) -> dict | None:
        resp = requests.get(
            "https://www.verkkokauppa.com/fi/search",
            params={"query": query},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return self._parse_store_page(resp.text, "Verkkokauppa.com")

    def _gigantti(self, query: str) -> dict | None:
        resp = requests.get(
            "https://www.gigantti.fi/search/",
            params={"q": query},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return self._parse_store_page(resp.text, "Gigantti")

    def _power(self, query: str) -> dict | None:
        resp = requests.get(
            "https://www.power.fi/search/",
            params={"q": query},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return self._parse_store_page(resp.text, "Power")

    # ── Parsijat ──────────────────────────────────────────────────────────

    def _parse_store_page(self, html: str, store: str) -> dict | None:
        """Yleinen hinnanpoimija kauppasivulta."""
        soup = BeautifulSoup(html, "html.parser")

        # 1) Structured data (schema.org) – tarkin
        for tag in soup.find_all(attrs={"itemprop": "price"}):
            val = tag.get("content") or tag.get_text(strip=True)
            price = self._to_int_price(val)
            if price and price > 5:
                name_tag = soup.find(attrs={"itemprop": "name"})
                name = name_tag.get_text(strip=True)[:80] if name_tag else store
                return {"price": price, "store": store, "product_name": name}

        # 2) Hae kaikki hinnat sivulta, palauta pienin järkevä
        all_text = soup.get_text()
        prices = self._extract_all_prices(all_text)

        if prices:
            # Suodata outlierit (ei alle 10 € eikä yli 50 000 €)
            reasonable = [p for p in sorted(prices) if 10 <= p <= 50_000]
            if reasonable:
                # Ota kolmanneksen pienimmistä mediaani (tuotelistat, ensimmäiset tulokset)
                bucket = reasonable[: max(1, len(reasonable) // 3)]
                price = sorted(bucket)[len(bucket) // 2]
                return {"price": price, "store": store, "product_name": store}

        return None

    # ── Apumetodit ────────────────────────────────────────────────────────

    @staticmethod
    def _clean_query(name: str) -> str:
        """Siivoa hakutermi: poista ylimääräiset selitteet."""
        # Poista sulkeiden sisältö
        name = re.sub(r"\([^)]*\)", "", name)
        # Poista yleisiä suomalaisia lisäsanoja
        junk = re.compile(
            r"\b(myydään|hyvä|kunto|toimiva|käytetty|uusi|uutta|vastaava|"
            r"halpa|tarjous|ilmainen)\b",
            re.IGNORECASE,
        )
        name = junk.sub("", name).strip()
        # Max 5 sanaa jotta haku on tarkka
        words = name.split()
        return " ".join(words[:5])

    @staticmethod
    def _extract_all_prices(text: str) -> list[int]:
        """Poimii kaikki hinnat tekstistä."""
        prices = []
        # "1 299,00 €" tai "1299€" tai "299,99 €"
        for m in re.finditer(r"(\d[\d\s]{0,6})[,\.](\d{2})\s*€", text):
            try:
                prices.append(int(m.group(1).replace(" ", "")))
            except ValueError:
                pass
        for m in re.finditer(r"(\d[\d\s]{0,5})\s*€", text):
            try:
                prices.append(int(m.group(1).replace(" ", "")))
            except ValueError:
                pass
        return prices

    @staticmethod
    def _to_int_price(val: str | None) -> int | None:
        if not val:
            return None
        val = str(val).strip().replace(" ", "").replace(",", ".")
        val = re.sub(r"[^0-9.]", "", val)
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None
