"""
AI-analysaattori – käyttää Claude Haikua (claude-haiku-4-5-20251001).

Kustannus per ilmoitus: ~0.0002–0.0005 € (1 000–3 000 tokenia)
1 000 ilmoitusta/kk ≈ 0.20–0.50 €
"""
import json
import logging
import re
import anthropic

logger = logging.getLogger(__name__)

PROMPT = """\
Olet suomalainen kirpputorimyyntiasiantuntija. Analysoi tämä Tori.fi-ilmoitus.

Ilmoitus:
- Otsikko:  {title}
- Hinta:    {price}
- Sijainti: {location}
- Kuvaus:   {description}

Palauta AINOASTAAN validi JSON-objekti – ei markdown-koodia, ei muuta tekstiä:
{{
  "product_name_normalized": "standardoitu hakulauseke englanniksi tai suomeksi, esim. 'Sony WH-1000XM5 kuulokkeet' tai 'iPhone 13 128GB'",
  "product_category": "elektroniikka" tai "urheilu" tai "kodinkoneet" tai "muu",
  "condition_score": <kokonaisluku 1–5: 1=rikki/puuttuu osia, 2=huono kuluma, 3=toimiva/normaalikulunut, 4=hyvä, 5=erinomainen/uutta vastaava>,
  "condition_reasoning": "1–2 lausetta perustelu kuntoluokalle",
  "estimated_resale_price": <realistinen myyntihinta tori.fi:ssä/vintedissä nyt, euroina, kokonaisluku>,
  "resale_reasoning": "1–2 lausetta hinta-arvion perusteluksi",
  "red_flags": "varoitusmerkit tai 'ei huomautettavaa'"
}}\
"""


class ListingAnalyzer:
    # Halvin Claude-malli – riittävä tekstianalyysiin
    MODEL     = "claude-haiku-4-5-20251001"
    MAX_TOKENS = 600

    def __init__(self, config):
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def analyze(self, listing: dict) -> dict | None:
        """
        Analysoi ilmoituksen AI:lla.
        Palauttaa dict tai None virheen sattuessa.
        """
        price_str = "Ilmainen" if listing["price"] == 0 else (
            "Hinta tuntematon" if listing["price"] < 0 else f"{listing['price']}€"
        )

        prompt = PROMPT.format(
            title       = (listing.get("title") or "")[:120],
            price       = price_str,
            location    = (listing.get("location") or "")[:60],
            description = (listing.get("description") or "")[:400],
        )

        try:
            response = self.client.messages.create(
                model      = self.MODEL,
                max_tokens = self.MAX_TOKENS,
                messages   = [{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()

            # Siivoa mahdolliset markdown-koodiblokkit
            raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

            data = json.loads(raw)

            # Varmista pakolliset kentät
            for key in ("product_name_normalized", "condition_score", "estimated_resale_price"):
                if key not in data:
                    logger.error(f"AI-vastaus puuttuu kenttä: {key}")
                    return None

            data["condition_score"]       = int(data["condition_score"])
            data["estimated_resale_price"] = int(data["estimated_resale_price"])

            return data

        except json.JSONDecodeError as exc:
            logger.error(f"AI palautti virheellistä JSONia: {exc} | raakateksti: {raw[:200]}")
            return None
        except anthropic.APIError as exc:
            logger.error(f"Anthropic API-virhe: {exc}")
            return None
        except Exception as exc:
            logger.error(f"Tuntematon virhe analyysissa: {exc}", exc_info=True)
            return None
