"""
Toripotti – automaattinen tori.fi-hakuvahtien analysaattori
"""
import logging
import sys
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


def process_listing(listing, fetcher, analyzer, alerter, config) -> bool:
    price_display = (
        "ILMAINEN" if listing["price"] == 0
        else "?"    if listing["price"] < 0
        else        f"{listing['price']}€"
    )
    logger.info(f"    📦 [{price_display}] {listing['title'][:70]}")

    analysis = analyzer.analyze(listing)
    if not analysis:
        logger.warning("       ⚠️  AI-analyysi epäonnistui, ohitetaan")
        return False

    logger.info(
        f"       🤖 Kunto: {analysis['condition_score']}/5 | "
        f"Arvioitu myynti: {analysis['estimated_resale_price']}€ | "
        f"{analysis['product_name_normalized']}"
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
        profit_pct   = (resale_price - buy_price) / buy_price * 100
        abs_profit   = resale_price - buy_price
        should_alert = (profit_pct >= config.min_profit_pct and abs_profit >= config.min_profit_eur)
    else:
        profit_pct, should_alert = 0.0, False

    if should_alert:
        alerter.send(listing, analysis, new_price_data, profit_pct)
        tag = "ILMAINEN 🎁" if profit_pct >= 9999 else f"+{profit_pct:.0f}%"
        logger.info(f"       🚨 HÄLYTYS LÄHETETTY [{tag}]")
        return True
    else:
        logger.info(f"       ✅ Ei riittävää potentiaalia ({profit_pct:.0f}%), ohitetaan")
        return False


def main():
    config = Config()

    reader   = GmailReader(config)
    parser   = ToriEmailParser()
    fetcher  = PriceFetcher()
    analyzer = ListingAnalyzer(config)
    alerter  = AlertSender(config)

    logger.info("🔍 Toripotti käynnistyy...")

    emails = reader.fetch_unread_tori_emails()
    logger.info(f"📬 Löytyi {len(emails)} käsittelemätöntä hakuvahti-emailiä")

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
                logger.warning("  ⚠️  Ei ilmoituksia parsittu tästä emailista")
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
