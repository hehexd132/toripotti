"""
AI-analysaattori – Claude Haiku + tori.fi oikeat markkinahinnat.

KORJAUKSET:
- condition_score None ei kaada ohjelmaa
- Tiukempi JSON-validointi ennen int()-muunnosta
"""
import json
import logging
import re
import time

import anthropic

from toripotti.tori_price_fetcher import ToriPriceFetcher

logger = logging.getLogger(__name__)

PROMPT = """\
Olet suomalainen kirpputorimyyntiasiantuntija joka tuntee Tori.fi-markkinan hyvin.

Ilmoitus:
- Otsikko:  {title}
- Hinta:    {price}
- Sijainti: {location}
- Kuvaus:   {description}

Tori.fi markkinahintatiedot (KÄYTETTYINÄ myytävät juuri nyt):
{tori_market}

TÄRKEÄÄ OHJEISTUS KATEGORIAN MUKAAN:
{category_guidance}

Palauta AINOASTAAN validi JSON – ei markdown, ei muuta tekstiä:
{{
  "product_name_normalized": "standardoitu hakulauseke, esim. 'iPhone 12 mini 64GB'",
  "product_category": "elektroniikka" tai "urheilu" tai "kodinkoneet" tai "muu",
  "condition_score": <kokonaisluku 1-5, EI null>,
  "condition_reasoning": "1-2 lausetta",
  "estimated_resale_price": <kokonaisluku euroina, EI null>,
  "resale_reasoning": "1-2 lausetta"
}}\
"""

CATEGORY_GUIDANCE = {
    "elektroniikka": (
        "Ole KONSERVATIIVINEN. Käytetty iPhone/MacBook/Samsung myy hitaasti "
        "ja hinta laskee jatkuvasti. Käytä tori.fi-dataa ensisijaisesti. "
        "Älä yliarvioi – mieluummin matala kuin korkea."
    ),
    "urheilu": (
        "Urheiluvälineet myyvät hyvin. Merkkituotteet (Trek, Specialized, "
        "Garmin, Suunto) pitävät arvonsa. Voit olla realistisen optimistinen."
    ),
    "kodinkoneet": (
        "Toimivuus ja ikä ratkaisevat. Ole realistinen."
    ),
    "muu": (
        "Arvioi realistisesti tori.fi-datan perusteella."
    ),
}


class ListingAnalyzer:
    MODEL      = "claude-haiku-4-5-20251001"
    MAX_TOKENS = 500
    SLEEP_SECS = 3.5   # Pidempi tauko → vähemmän 429-virheitä

    def __init__(self, config):
        self.client       = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self.tori_fetcher = ToriPriceFetcher()

    def analyze(self, listing: dict) -> dict | None:
        price_str = (
            "Ilmainen" if listing["price"] == 0
            else "Hinta tuntematon" if listing["price"] < 0
            else f"{listing['price']}€"
        )

        title       = listing.get("title", "")
        tori_data   = self.tori_fetcher.fetch(
            title,
            max_price=listing["price"] * 3 if listing["price"] > 0 else None,
        )
        tori_market   = self._format_tori_data(tori_data)
        pre_category  = self._guess_category(title)

        prompt = PROMPT.format(
            title             = title[:120],
            price             = price_str,
            location          = (listing.get("location") or "")[:60],
            description       = (listing.get("description") or "")[:400],
            tori_market       = tori_market,
            category_guidance = CATEGORY_GUIDANCE.get(pre_category, CATEGORY_GUIDANCE["muu"]),
        )

        # Tauko ennen API-kutsua
        time.sleep(self.SLEEP_SECS)

        try:
            response = self.client.messages.create(
                model      = self.MODEL,
                max_tokens = self.MAX_TOKENS,
                messages   = [{"role": "user", "content": prompt}],
            )
            raw  = response.content[0].text.strip()
            raw  = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
            data = json.loads(raw)

            # Validoi ja muunna kentät turvallisesti
            required = ("product_name_normalized", "condition_score", "estimated_resale_price")
            for key in required:
                if key not in data:
                    logger.error(f"AI-vastaus puuttuu kenttä: {key}")
                    return None

            # Turvallinen int-muunnos – None tai virheellinen arvo → hylkää
            try:
                cs = data["condition_score"]
                data["condition_score"] = int(cs) if cs is not None else None
                if data["condition_score"] is None:
                    logger.error("condition_score on null")
                    return None
            except (TypeError, ValueError):
                logger.error(f"condition_score ei ole numero: {data['condition_score']}")
                return None

            try:
                rp = data["estimated_resale_price"]
                data["estimated_resale_price"] = int(rp) if rp is not None else None
                if data["estimated_resale_price"] is None:
                    logger.error("estimated_resale_price on null")
                    return None
            except (TypeError, ValueError):
                logger.error(f"estimated_resale_price ei ole numero: {data['estimated_resale_price']}")
                return None

            data["tori_market_data"] = tori_data
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

    @staticmethod
    def _format_tori_data(data: dict | None) -> str:
        if not data:
            return "Ei hakutuloksia tori.fi:stä – arvioi oman tietämyksesi perusteella."
        return (
            f"Tori.fi ({data['count']} ilmoitusta): "
            f"alin {data['low']}€ / mediaani {data['median']}€ / korkein {data['high']}€"
        )

    @staticmethod
    def _guess_category(title: str) -> str:
        t = title.lower()
        if any(w in t for w in [
            "iphone", "ipad", "macbook", "imac", "samsung", "huawei", "pixel", "xiaomi",
            "oneplus", "sony", "laptop", "kannettava", "tabletti", "puhelin", "tietokone",
            "airpods", "kuulokkeet", "näyttö", "monitor", "gpu", "cpu", "rtx", "rx ",
            "ps5", "playstation", "xbox", "nintendo", "kamera", "objektiivi", "pelikone",
            "näytönohjain", "prosessori", "emolevy", "focusrite", "plotteri", "tulostin",
            "apple watch", "älykello",
        ]):
            return "elektroniikka"
        if any(w in t for w in [
            "pyörä", "polkupyörä", "fillari", "maastopyörä", "sähköpyörä",
            "juoksukengät", "kuntosali", "treeni", "tennis", "golf",
            "sukset", "lumilaudat", "garmin", "suunto", "polar", "fitbit",
            "skeittilauta", "sup", "kajak", "urheil",
        ]):
            return "urheilu"
        if any(w in t for w in [
            "pyykinpesukone", "kuivausrumpu", "astianpesukone", "jääkaappi",
            "pakastin", "liesi", "uuni", "mikroaaltouuni", "imuri",
        ]):
            return "kodinkoneet"
        return "muu"
