"""
AI-analysaattori – Claude Haiku + nopea web-hinta.

KORJAUS: Vain 1 web-haku per ilmoitus (Huuto.net) entisen 3:n sijaan.
→ Nopea, ei aikakatkaisuriskiä, silti oikeaa markkinadataa.
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

Käytettyjen tuotteiden markkinahintatiedot (Huuto.net ja muu verkko):
{market_data}

Palauta AINOASTAAN validi JSON – ei markdown-koodia, ei muuta tekstiä:
{{
  "product_name_normalized": "standardoitu hakulauseke, esim. 'Sony WH-1000XM5' tai 'iPhone 13 128GB'",
  "product_category": "elektroniikka" tai "urheilu" tai "kodinkoneet" tai "muu",
  "condition_score": <1–5: 1=rikki, 2=huono, 3=toimiva, 4=hyvä, 5=erinomainen>,
  "condition_reasoning": "1–2 lausetta",
  "estimated_resale_price": <realistinen myyntihinta tori.fi:ssä nyt euroina, kokonaisluku>,
  "resale_reasoning": "1–2 lausetta – mainitse jos pohjautuu markkinadataan",
  "red_flags": "varoitusmerkit tai 'ei huomautettavaa'"
}}\
"""


class ListingAnalyzer:
    MODEL      = "claude-haiku-4-5-20251001"
    MAX_TOKENS = 700
    TIMEOUT    = 8

    def __init__(self, config):
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def analyze(self, listing: dict) -> dict | None:
        price_str = (
            "Ilmainen" if listing["price"] == 0
            else "Hinta tuntematon" if listing["price"] < 0
            else f"{listing['price']}€"
        )

        query       = self._clean_query(listing.get("title", ""))
        market_data = self._fetch_used_price(query)

        prompt = PROMPT.format(
            title       = (listing.get("title") or "")[:120],
            price       = price_str,
            location    = (listing.get("location") or "")[:60],
            description = (listing.get("description") or "")[:400],
            market_data = market_data or "Ei markkinadataa saatavilla – arvioi oman tietämyksesi perusteella.",
        )

        try:
            response = self.client.messages.create(
                model      = self.MODEL,
                max_tokens = self.MAX_TOKENS,
                messages   = [{"role": "user", "content": prompt}],
            )
            raw  = response.content[0].text.strip()
            raw  = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
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
            logger.error(f"Analyysivirhe: {exc}", exc_info=True)
            return None

    # ── Yksi nopea web-haku ───────────────────────────────────────────────

    def _fetch_used_price(self, query: str) -> str | None:
        """
        Hakee käytettyjen hinnat Huuto.netistä.
        Nopea (1 pyyntö), hyvä suomalainen data.
        """
        if not query or len(query) < 3:
            return None
        try:
            resp = requests.get(
                "https://www.huuto.net/haku",
                params={"sana": query, "sivu": 1},
                headers=HEADERS,
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            soup   = BeautifulSoup(resp.text, "html.parser")
            prices = self._extract_prices(soup.get_text())

            if not prices:
                return None

            prices_sorted = sorted(p for p in prices if 3 < p < 30_000)
            if not prices_sorted:
                return None

            low    = prices_sorted[0]
            median = prices_sorted[len(prices_sorted) // 2]
            high   = prices_sorted[-1]
            n      = len(prices_sorted)
            return (
                f"Huuto.net ({n} myyntiä): alin {low}€ / mediaani {median}€ / korkein {high}€"
            )

        except requests.RequestException as exc:
            logger.debug(f"Huuto.net haku epäonnistui: {exc}")
            return None
        except Exception as exc:
            logger.debug(f"Hintahaku virhe: {exc}")
            return None

    # ── Apumetodit ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_prices(text: str) -> list[int]:
        prices = []
        for m in re.finditer(r"(?<!\d)(\d{1,5})\s*€", text):
            try:
                val = int(m.group(1))
                if 3 < val < 30_000:
                    prices.append(val)
            except ValueError:
                pass
        return prices

    @staticmethod
    def _clean_query(name: str) -> str:
        name = re.sub(r"\([^)]*\)", "", name)
        junk = re.compile(
            r"\b(myydään|hyvä|kunto|toimiva|käytetty|uusi|uutta|vastaava|"
            r"halpa|tarjous|ilmainen|myynnissä|vaihto|etsitään|ostetaan)\b",
            re.IGNORECASE,
        )
        name = junk.sub("", name).strip()
        return " ".join(name.split()[:5])
