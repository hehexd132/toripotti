"""
AI-analysaattori – Claude Haiku + web search käytetyille hinnoille.

Ensin haetaan oikeat käytetyt hinnat netistä (Hintaseuranta, Pricespy,
Google Shopping), sitten Claude arvioi realistisen myyntihinnan
oikean markkinadatan pohjalta.

Kustannus per ilmoitus: ~0.0005–0.001 € (web search + analyysi)
"""
import json
import logging
import re
import time

import anthropic
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
}

PROMPT = """\
Olet suomalainen kirpputorimyyntiasiantuntija. Analysoi tämä Tori.fi-ilmoitus.

Ilmoitus:
- Otsikko:  {title}
- Hinta:    {price}
- Sijainti: {location}
- Kuvaus:   {description}

Käytettyjen tuotteiden markkinahintatiedot netistä:
{market_data}

Arvioi realistinen myyntihinta OTTAEN HUOMIOON yllä oleva markkinadata.
Jos markkinadata on tyhjä tai epäluotettava, käytä omaa tietämystäsi.

Palauta AINOASTAAN validi JSON – ei markdown-koodia, ei muuta tekstiä:
{{
  "product_name_normalized": "standardoitu hakulauseke, esim. 'Sony WH-1000XM5' tai 'iPhone 13 128GB'",
  "product_category": "elektroniikka" tai "urheilu" tai "kodinkoneet" tai "muu",
  "condition_score": <1–5: 1=rikki, 2=huono, 3=toimiva, 4=hyvä, 5=erinomainen>,
  "condition_reasoning": "1–2 lausetta",
  "estimated_resale_price": <realistinen myyntihinta tori.fi:ssä nyt, kokonaisluku euroina>,
  "resale_reasoning": "1–2 lausetta – mainitse jos pohjautuu markkinadataan",
  "red_flags": "varoitusmerkit tai 'ei huomautettavaa'"
}}\
"""


class ListingAnalyzer:
    MODEL      = "claude-haiku-4-5-20251001"
    MAX_TOKENS = 700
    TIMEOUT    = 10

    def __init__(self, config):
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def analyze(self, listing: dict) -> dict | None:
        price_str = (
            "Ilmainen" if listing["price"] == 0
            else "Hinta tuntematon" if listing["price"] < 0
            else f"{listing['price']}€"
        )

        # 1) Hae käytettyjen hinnat netistä
        product_hint = listing.get("title", "")
        market_data  = self._fetch_used_prices(product_hint)

        prompt = PROMPT.format(
            title       = (listing.get("title") or "")[:120],
            price       = price_str,
            location    = (listing.get("location") or "")[:60],
            description = (listing.get("description") or "")[:400],
            market_data = market_data or "Ei markkinadataa saatavilla.",
        )

        try:
            response = self.client.messages.create(
                model      = self.MODEL,
                max_tokens = self.MAX_TOKENS,
                messages   = [{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()
            raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
            data = json.loads(raw)

            for key in ("product_name_normalized", "condition_score", "estimated_resale_price"):
                if key not in data:
                    logger.error(f"AI-vastaus puuttuu kenttä: {key}")
                    return None

            data["condition_score"]        = int(data["condition_score"])
            data["estimated_resale_price"] = int(data["estimated_resale_price"])
            return data

        except json.JSONDecodeError as exc:
            logger.error(f"AI palautti virheellistä JSONia: {exc}")
            return None
        except anthropic.APIError as exc:
            logger.error(f"Anthropic API-virhe: {exc}")
            return None
        except Exception as exc:
            logger.error(f"Tuntematon virhe analyysissa: {exc}", exc_info=True)
            return None

    # ── Käytettyjen hintojen haku ─────────────────────────────────────────

    def _fetch_used_prices(self, product_name: str) -> str:
        """
        Hakee käytettyjen tuotteiden hintoja useasta lähteestä.
        Palauttaa tekstiyhteenvedon Claudelle syötettäväksi.
        """
        if not product_name or len(product_name) < 4:
            return ""

        query = self._clean_query(product_name)
        results = []

        sources = [
            ("Hintaseuranta.fi", self._hintaseuranta),
            ("Huuto.net",        self._huuto),
            ("Google Shopping",  self._google_shopping),
        ]

        for name, fn in sources:
            try:
                data = fn(query)
                if data:
                    results.append(f"[{name}]: {data}")
                time.sleep(0.5)
            except Exception as exc:
                logger.debug(f"  {name} haku epäonnistui: {exc}")

        if not results:
            return ""

        return "\n".join(results)

    def _hintaseuranta(self, query: str) -> str | None:
        """Hintaseuranta.fi – suomalainen hintavertailu."""
        resp = requests.get(
            "https://www.hintaseuranta.fi/haku",
            params={"q": query},
            headers=HEADERS,
            timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        soup  = BeautifulSoup(resp.text, "html.parser")
        prices = self._extract_prices_from_soup(soup)
        if prices:
            median = sorted(prices)[len(prices) // 2]
            return f"Uutena ~{median}€ (mediaani {len(prices)} tuloksesta)"
        return None

    def _huuto(self, query: str) -> str | None:
        """Huuto.net – suomalainen huutokauppa, hyvä käytettyjen hinnoille."""
        resp = requests.get(
            "https://www.huuto.net/haku",
            params={"sana": query, "sivu": 1},
            headers=HEADERS,
            timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        soup   = BeautifulSoup(resp.text, "html.parser")
        prices = self._extract_prices_from_soup(soup)
        if prices:
            prices_sorted = sorted(prices)
            low    = prices_sorted[0]
            high   = prices_sorted[-1]
            median = prices_sorted[len(prices_sorted) // 2]
            return f"Käytettynä: alin {low}€ / mediaani {median}€ / korkein {high}€ ({len(prices)} myyntiä)"
        return None

    def _google_shopping(self, query: str) -> str | None:
        """
        Google Shopping -haku käytetyille tuotteille.
        Hakee 'käytetty [tuote] hinta' -termillä.
        """
        search_query = f"{query} käytetty hinta"
        resp = requests.get(
            "https://www.google.fi/search",
            params={"q": search_query, "tbm": "shop", "hl": "fi"},
            headers=HEADERS,
            timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        soup   = BeautifulSoup(resp.text, "html.parser")
        prices = self._extract_prices_from_soup(soup)
        if prices:
            prices_sorted = sorted(p for p in prices if p > 5)[:10]
            if prices_sorted:
                median = prices_sorted[len(prices_sorted) // 2]
                return f"Google Shopping käytetty: ~{median}€ (mediaani)"
        return None

    # ── Apumetodit ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_prices_from_soup(soup: BeautifulSoup) -> list[int]:
        prices = []
        text   = soup.get_text()
        for m in re.finditer(r"(\d[\d\s]{0,5})[,\.](\d{2})\s*€", text):
            try:
                prices.append(int(m.group(1).replace(" ", "")))
            except ValueError:
                pass
        for m in re.finditer(r"(\d[\d\s]{0,5})\s*€", text):
            try:
                val = int(m.group(1).replace(" ", ""))
                if 5 < val < 50_000:
                    prices.append(val)
            except ValueError:
                pass
        return prices

    @staticmethod
    def _clean_query(name: str) -> str:
        name = re.sub(r"\([^)]*\)", "", name)
        junk = re.compile(
            r"\b(myydään|hyvä|kunto|toimiva|käytetty|uusi|uutta|vastaava|"
            r"halpa|tarjous|ilmainen|myynnissä|vaihto)\b",
            re.IGNORECASE,
        )
        name = junk.sub("", name).strip()
        return " ".join(name.split()[:5])
