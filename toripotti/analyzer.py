"""
AI-analysaattori – Claude Haiku + tori.fi oikeat markkinahinnat.

UUTTA:
- Hakee käytetyn hinnan tori.fi:stä (realistisin lähde)
- Kategoriakohtainen prompt – Claude tietää kategorian kontekstin
- 2 sek tauko ennen API-kutsua välttää 429-virheet
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
  "product_name_normalized": "standardoitu hakulauseke hakua varten, esim. 'iPhone 12 mini 64GB' tai 'Trek FX3'",
  "product_category": "elektroniikka" | "urheilu" | "kodinkoneet" | "muu",
  "condition_score": <1–5: 1=rikki, 2=huono, 3=toimiva, 4=hyvä, 5=erinomainen>,
  "condition_reasoning": "1–2 lausetta",
  "estimated_resale_price": <realistinen myyntihinta tori.fi:ssä nyt, kokonaisluku euroina>,
  "resale_reasoning": "1–2 lausetta – kerro perustuuko tori.fi-dataan vai omaan arvioon"
}}\
"""

# Ohjeet kategoriakohtaisesti
CATEGORY_GUIDANCE = {
    "elektroniikka": (
        "Elektroniikka (erit. Apple-tuotteet): Ole KONSERVATIIVINEN hinta-arviossa. "
        "Käytetty iPhone tai MacBook myy hitaasti ja hinta laskee jatkuvasti. "
        "Käytä tori.fi markkinadataa ensisijaisesti – se kertoo todellisen myyntihinnan. "
        "Älä yliarvioi. Mieluummin liian matala kuin liian korkea arvio."
    ),
    "urheilu": (
        "Urheiluvälineet: Pyörät, kuntosalilaitteet, ulkoiluvarusteet myyvät hyvin. "
        "Merkkituotteet (Trek, Specialized, Garmin, Suunto) pitävät arvonsa. "
        "Voit olla realistisen optimistinen hyvässä kunnossa olevista merkkituotteista."
    ),
    "kodinkoneet": (
        "Kodinkoneet: Käytetyt kodinkoneet myyvät nopeasti jos hinta on oikea. "
        "Ole realistinen – toimivuus ja ikä ratkaisevat enemmän kuin brändi."
    ),
    "muu": (
        "Muut tuotteet: Arvioi realistisesti tori.fi-datan perusteella. "
        "Jos ei markkinadataa, käytä omaa tietämystäsi varovaisesti."
    ),
}


class ListingAnalyzer:
    MODEL      = "claude-haiku-4-5-20251001"
    MAX_TOKENS = 600

    def __init__(self, config):
        self.client       = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self.tori_fetcher = ToriPriceFetcher()

    def analyze(self, listing: dict) -> dict | None:
        price_str = (
            "Ilmainen" if listing["price"] == 0
            else "Hinta tuntematon" if listing["price"] < 0
            else f"{listing['price']}€"
        )

        # Hae tori.fi markkinahinnat
        title       = listing.get("title", "")
        tori_data   = self.tori_fetcher.fetch(title, max_price=listing["price"] * 3 if listing["price"] > 0 else None)
        tori_market = self._format_tori_data(tori_data)

        # Arvaa kategoria otsikosta ennen AI-kutsua → parempi ohjeistus
        pre_category = self._guess_category(title)

        prompt = PROMPT.format(
            title             = title[:120],
            price             = price_str,
            location          = (listing.get("location") or "")[:60],
            description       = (listing.get("description") or "")[:400],
            tori_market       = tori_market,
            category_guidance = CATEGORY_GUIDANCE.get(pre_category, CATEGORY_GUIDANCE["muu"]),
        )

        # Tauko ennen API-kutsua – välttää 429-virheet
        time.sleep(2.0)

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

            # Tallenna tori-data tulokseen (näkyy hälytyksessä)
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

    # ── Apumetodit ────────────────────────────────────────────────────────

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
            "iphone", "ipad", "macbook", "samsung", "huawei", "pixel", "xiaomi",
            "sony", "laptop", "kannettava", "tabletti", "puhelin", "tietokone",
            "airpods", "kuulokkeet", "näyttö", "monitor", "gpu", "cpu", "ps5",
            "playstation", "xbox", "nintendo", "kamera", "objektiivi",
        ]):
            return "elektroniikka"
        if any(w in t for w in [
            "pyörä", "polkupyörä", "fillari", "maastopyörä", "juoksukengät",
            "kuntosali", "treeni", "urheilu", "tennis", "golf", "sukset",
            "lumilaudat", "garmin", "suunto", "polar", "fitbit",
            "sähköpyörä", "skeittilauta", "sup", "kajak",
        ]):
            return "urheilu"
        if any(w in t for w in [
            "pyykinpesukone", "kuivausrumpu", "astianpesukone", "jääkaappi",
            "pakastin", "liesi", "uuni", "mikroaaltouuni", "imuri", "robotti-imuri",
        ]):
            return "kodinkoneet"
        return "muu"
