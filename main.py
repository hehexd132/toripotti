"""
Toripotti – automaattinen tori.fi-hakuvahtien analysaattori
Ajaa: GitHub Actions (joka 30 min, ilmainen)
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


def main():
    config = Config()

    reader   = GmailReader(config)
    parser   = ToriEmailParser()
    fetcher  = PriceFetcher()
    analyzer = ListingAnalyzer(config)
    alerter  = AlertSender(config)

    logger.info("🔍 Toripotti käynnistyy...")

    emails = reader.fetch_unread_tori_emails()
    logger.info(f"📬 Löytyi {len(emails)} käsittelemätöntä hakuvahti-ilmoitusta")

    if not emails:
        logger.info("Ei uusia ilmoituksia. Lopetetaan.")
        return

    alerts_sent = 0

    for email_data in emails:
        try:
            listing = parser.parse(email_data)
            if not listing:
                logger.warning("⚠️  Parsiminen epäonnistui, ohitetaan")
                continue

            price_display = "ILMAINEN" if listing["price"] == 0 else f"{listing['price']}€"
            logger.info(f"📦 [{price_display}] {listing['title']}")

            # AI-analyysi (Claude Haiku, ~0.0002€/kpl)
            analysis = analyzer.analyze(listing)
            if not analysis:
                logger.warning("   ⚠️  AI-analyysi epäonnistui, ohitetaan")
                continue

            logger.info(
                f"   🤖 Kunto: {analysis['condition_score']}/5 | "
                f"Tuote: {analysis['product_name_normalized']} | "
                f"Arvioitu myynti: {analysis['estimated_resale_price']}€"
            )

            # Ohita liian huonokuntoiset
            if analysis["condition_score"] <= 1:
                logger.info("   ❌ Kunto liian huono (1/5), ohitetaan")
                continue

            # Hae uuden tuotteen hinta verkkokaupoista
            new_price_data = fetcher.search_price(analysis["product_name_normalized"])
            if new_price_data:
                logger.info(f"   🏪 Uutena: {new_price_data['price']}€ @ {new_price_data['store']}")

            # Laske tuottopotentiaali
            buy_price    = listing["price"]
            resale_price = analysis["estimated_resale_price"]

            if buy_price == 0 and resale_price >= 20:
                # Ilmainen tavara + kohtuullinen arvo = aina hälytys
                profit_pct    = 9999.0
                should_alert  = True
            elif buy_price > 0 and resale_price > buy_price:
                profit_pct   = (resale_price - buy_price) / buy_price * 100
                abs_profit   = resale_price - buy_price
                should_alert = profit_pct >= config.min_profit_pct and abs_profit >= config.min_profit_eur
            else:
                profit_pct   = 0.0
                should_alert = False

            if should_alert:
                alerter.send(listing, analysis, new_price_data, profit_pct)
                alerts_sent += 1
                tag = "ILMAINEN 🎁" if profit_pct >= 9999 else f"+{profit_pct:.0f}%"
                logger.info(f"   🚨 HÄLYTYS LÄHETETTY [{tag}]")
            else:
                logger.info(f"   ✅ Ei riittävää potentiaalia ({profit_pct:.0f}%), ohitetaan")

        except Exception as exc:
            logger.error(f"❌ Virhe ilmoituksen käsittelyssä: {exc}", exc_info=True)

    logger.info(f"✅ Valmis. Hälytyksset lähetetty: {alerts_sent} / {len(emails)}")


if __name__ == "__main__":
    main()
