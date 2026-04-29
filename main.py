"""
Toripotti v5 – kategoriakohtaiset kynnykset + erätunnistus

Kynnykset:
  elektroniikka  → 60% + 25€  (Apple-tuotteet yliarvioituvat helposti)
  urheilu        → 40% + 15€  (merkkiurheiluvälineet myyvät hyvin)
  kodinkoneet    → 45% + 20€
  muu            → 40% + 15€  (lievennettty yleiskynnys)

Erätarjoukset (is_bulk=True):
  → Kynnys puolitetaan – 2 kpl tavaraa pienellä hinnalla on usein hyvä diili
"""
import logging
import sys
import time

from toripotti.config import Config
from toripotti.gmail_reader import GmailReader
from toripotti.email_parser import ToriEmailParser
from toripotti.price_fetcher import PriceFetcher
from toripotti.analyzer import ListingAnalyzer
from toripotti.alerter import AlertSender

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Kategoriakohtaiset kynnykset: (min_profit_pct, min_profit_eur)
CATEGORY_THRESHOLDS: dict[str, tuple[float, float]] = {
    "elektroniikka": (60.0, 25.0),
    "urheilu":       (40.0, 15.0),
    "kodinkoneet":   (45.0, 20.0),
    "muu":           (40.0, 15.0),
}

# Erätarjouksille kynnykset puolitetaan
BULK_DIVISOR = 2.0


def get_thresholds(category: str, is_bulk: bool) -> tuple[float, float]:
    pct, eur = CATEGORY_THRESHOLDS.get(category, CATEGORY_THRESHOLDS["muu"])
    if is_bulk:
        pct = pct / BULK_DIVISOR
        eur = eur / BULK_DIVISOR
    return pct, eur


def process_listing(listing, fetcher, analyzer, alerter, config) -> bool:
    price_display = (
        "ILMAINEN" if listing["price"] == 0
        else "?"    if listing["price"] < 0
        else        f"{listing['price']}€"
    )
    bulk_tag = " 📦[ERÄTARJOUS]" if listing.get("is_bulk") else ""
    logger.info(f"    📦 [{price_display}] {listing['title'][:70]}{bulk_tag}")

    analysis = analyzer.analyze(listing)
    if not analysis:
        logger.warning("       ⚠️  AI-analyysi epäonnistui, ohitetaan")
        return False

    category  = analysis.get("product_category", "muu")
    is_bulk   = listing.get("is_bulk", False)
    min_pct, min_eur = get_thresholds(category, is_bulk)

    tori_info = ""
    if analysis.get("tori_market_data"):
        d = analysis["tori_market_data"]
        tori_info = f" | Tori.fi mediaani: {d['median']}€ ({d['count']} ilm.)"

    logger.info(
        f"       🤖 [{category}] Kunto: {analysis['condition_score']}/5 | "
        f"Arvioitu myynti: {analysis['estimated_resale_price']}€{tori_info}"
    )

    if analysis["condition_score"] <= 1:
        logger.info("       ❌ Kunto liian huono, ohitetaan")
        return False

    new_price_data = fetcher.search_price(analysis["product_name_normalized"])
    if new_price_data:
        logger.info(f"       🏪 Uutena: {new_price_data['price']}€ @ {new_price_data['store']}")

    buy_price    = listing["price"]
    resale_price = analysis["estimated_resale_price"]

    if buy_price == 0 and resale_price >= 20:
        profit_pct, should_alert = 9999.0, True
    elif buy_price > 0 and resale_price > buy_price:
        profit_pct = (resale_price - buy_price) / buy_price * 100
        abs_profit = resale_price - buy_price
        should_alert = (profit_pct >= min_pct and abs_profit >= min_eur)
    else:
        profit_pct, should_alert = 0.0, False

    threshold_info = f"kynnys {min_pct:.0f}%/{min_eur:.0f}€"
    if is_bulk:
        threshold_info += " (erätarjous, puolitettu)"

    if should_alert:
        alerter.send(listing, analysis, new_price_data, profit_pct)
        tag = "ILMAINEN 🎁" if profit_pct >= 9999 else f"+{profit_pct:.0f}%"
        logger.info(f"       🚨 HÄLYTYS [{tag}] ({threshold_info})")
        return True
    else:
        logger.info(f"       ✅ Ei riittävää potentiaalia ({profit_pct:.0f}%, {threshold_info})")
        return False


def main():
    config = Config()

    reader   = GmailReader(config)
    parser   = ToriEmailParser()
    fetcher  = PriceFetcher()
    analyzer = ListingAnalyzer(config)
    alerter  = AlertSender(config)

    logger.info("🔍 Toripotti v5 käynnistyy...")
    logger.info("📊 Kynnykset: elektroniikka 60%/25€ | urheilu 40%/15€ | muu 40%/15€")
    logger.info("📦 Erätarjoukset: kynnys puolitetaan automaattisesti")

    emails = reader.fetch_unread_tori_emails()
    logger.info(f"📬 Löytyi {len(emails)} käsittelemätöntä emailiä")

    if not emails:
        logger.info("Ei uusia emailejä. Lopetetaan.")
        return

    total_listings = 0
    alerts_sent    = 0

    for i, email_data in enumerate(emails, 1):
        subject = email_data.get("subject", "")[:60]
        logger.info(f"\n── Email {i}/{len(emails)}: {subject}")

        try:
            listings = parser.parse_all(email_data)
            total_listings += len(listings)

            if not listings:
                logger.warning("  ⚠️  Ei ilmoituksia parsittu")
                continue

            for listing in listings:
                try:
                    if process_listing(listing, fetcher, analyzer, alerter, config):
                        alerts_sent += 1
                except Exception as exc:
                    logger.error(f"  ❌ Ilmoitusvirhe: {exc}", exc_info=True)

        except Exception as exc:
            logger.error(f"❌ Emailin käsittelyvirhe: {exc}", exc_info=True)

    logger.info(
        f"\n✅ Valmis. "
        f"Emaileja: {len(emails)} | "
        f"Ilmoituksia: {total_listings} | "
        f"Hälytyksiä: {alerts_sent}"
    )


if __name__ == "__main__":
    main()
