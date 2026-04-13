"""
Hälytysemail – lähetetään kun löytyy riittävä tuottopotentiaali.
Lähettää kauniin HTML-emailin jossa kaikki tärkeä tieto.
"""
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


class AlertSender:
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT   = 587

    def __init__(self, config):
        self.config = config

    def send(
        self,
        listing:       dict,
        analysis:      dict,
        new_price_data: dict | None,
        profit_pct:    float,
    ) -> None:
        try:
            msg            = MIMEMultipart("alternative")
            msg["Subject"] = self._subject(listing, profit_pct)
            msg["From"]    = self.config.smtp_user
            msg["To"]      = self.config.alert_to

            msg.attach(MIMEText(self._html(listing, analysis, new_price_data, profit_pct), "html", "utf-8"))

            with smtplib.SMTP(self.SMTP_SERVER, self.SMTP_PORT) as srv:
                srv.ehlo()
                srv.starttls()
                srv.login(self.config.smtp_user, self.config.smtp_password)
                srv.send_message(msg)

            logger.info(f"✉️  Hälytys lähetetty → {self.config.alert_to}")

        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP-autentikointivirhe – tarkista Gmail App Password!")
        except Exception as exc:
            logger.error(f"Hälytysemail epäonnistui: {exc}", exc_info=True)

    # ── Sähköpostin sisältö ───────────────────────────────────────────────

    @staticmethod
    def _subject(listing: dict, profit_pct: float) -> str:
        tag   = "ILMAINEN 🎁" if profit_pct >= 9999 else f"+{profit_pct:.0f}% 💰"
        title = (listing.get("title") or "Tuntematon")[:50]
        return f"🚨 Toripotti: {title} [{tag}]"

    @staticmethod
    def _html(
        listing:       dict,
        analysis:      dict,
        new_price_data: dict | None,
        profit_pct:    float,
    ) -> str:
        buy_price    = listing.get("price", 0)
        resale_price = analysis.get("estimated_resale_price", 0)
        condition    = analysis.get("condition_score", 0)
        stars        = "⭐" * condition + "☆" * (5 - condition)

        if profit_pct >= 9999:
            badge_text  = "ILMAINEN TAVARA"
            badge_color = "#2e7d32"
        elif profit_pct >= 100:
            badge_text  = f"+{profit_pct:.0f}% POTENTIAALI"
            badge_color = "#1565c0"
        else:
            badge_text  = f"+{profit_pct:.0f}% POTENTIAALI"
            badge_color = "#e65100"

        buy_display = "ILMAINEN" if buy_price == 0 else f"{buy_price} €"

        abs_profit = resale_price - buy_price if buy_price >= 0 else resale_price
        profit_display = f"+{abs_profit} €" if abs_profit > 0 else "?"

        new_price_row = ""
        if new_price_data:
            discount = ""
            if new_price_data["price"] > resale_price:
                discount = f"&nbsp;<span style='color:#888;font-size:.9em;'>(uutena {new_price_data['price']} €)</span>"
            new_price_row = f"""
            <tr>
              <td style='{TD}'>Uutena ({new_price_data['store']}):</td>
              <td style='{TD}'>{new_price_data['price']} €{discount}</td>
            </tr>"""

        tori_url   = listing.get("url") or ""
        button_html = (
            f'<a href="{tori_url}" style="display:inline-block;background:#e8612c;'
            f'color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;'
            f'font-weight:700;font-size:1em;">📱 Avaa tori.fi ilmoitus</a>'
        ) if tori_url else '<i style="color:#999;">Linkkiä ei saatavilla</i>'

        red_flags = analysis.get("red_flags", "")
        red_flags_html = ""
        if red_flags and red_flags.lower() not in ("ei huomautettavaa", "none", "no issues"):
            red_flags_html = f"""
            <div style='{BLOCK} border-left:4px solid #c62828;background:#ffebee;'>
              <b>⚠️ Huomiot:</b><br>{red_flags}
            </div>"""

        desc = (listing.get("description") or "")[:300]

        timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")

        return f"""<!DOCTYPE html>
<html lang="fi">
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:620px;margin:0 auto;
             padding:24px;background:#fafafa;color:#222;">

  <!-- Otsikkoalue -->
  <div style="background:{badge_color};color:#fff;padding:18px 22px;
              border-radius:10px;margin-bottom:22px;text-align:center;">
    <div style="font-size:0.85em;letter-spacing:.1em;opacity:.85;">TORIPOTTI HÄLYTYS</div>
    <div style="font-size:1.6em;font-weight:700;margin-top:4px;">{badge_text}</div>
    <div style="font-size:0.9em;margin-top:4px;opacity:.85;">{timestamp}</div>
  </div>

  <!-- Otsikko -->
  <h2 style="margin:0 0 18px;font-size:1.3em;color:#111;">
    {listing.get('title', '')}
  </h2>

  <!-- Numerot -->
  <table style="width:100%;border-collapse:collapse;margin-bottom:18px;">
    <tr style="background:#fff;">
      <td style="{TD}"><b>Ostohinta torista:</b></td>
      <td style="{TD};font-size:1.25em;color:#e8612c;font-weight:700;">{buy_display}</td>
    </tr>
    <tr style="background:#f5f5f5;">
      <td style="{TD}"><b>Arvioitu myyntihinta:</b></td>
      <td style="{TD};font-size:1.25em;color:#2e7d32;font-weight:700;">{resale_price} €</td>
    </tr>
    <tr style="background:#fff;">
      <td style="{TD}"><b>Arvioitu voitto:</b></td>
      <td style="{TD};font-size:1.1em;font-weight:700;">{profit_display}</td>
    </tr>
    {new_price_row}
    <tr style="background:#f5f5f5;">
      <td style="{TD}"><b>Kunto:</b></td>
      <td style="{TD};">{stars} &nbsp;({condition}/5)</td>
    </tr>
    <tr style="background:#fff;">
      <td style="{TD}"><b>Sijainti:</b></td>
      <td style="{TD};">{listing.get('location', '')}</td>
    </tr>
  </table>

  <!-- AI-analyysit -->
  <div style="{BLOCK} border-left:4px solid #f9a825;background:#fffde7;">
    <b>🔍 Kunto-arvio (AI):</b><br>
    {analysis.get('condition_reasoning', '')}
  </div>

  <div style="{BLOCK} border-left:4px solid #43a047;background:#e8f5e9;">
    <b>💰 Hinta-arvio (AI):</b><br>
    {analysis.get('resale_reasoning', '')}
  </div>

  {red_flags_html}

  <!-- Kuvaus -->
  <div style="{BLOCK} border-left:4px solid #90a4ae;background:#f5f5f5;">
    <b>📄 Ilmoitusteksti:</b><br>
    <span style="color:#555;">{desc}</span>
  </div>

  <!-- Nappi -->
  <div style="text-align:center;margin:26px 0;">
    {button_html}
  </div>

  <hr style="border:none;border-top:1px solid #ddd;margin:20px 0;">
  <p style="color:#aaa;font-size:0.75em;text-align:center;margin:0;">
    Toripotti · automaattinen hälytys · {timestamp}
  </p>
</body>
</html>"""


# CSS-vakiot (DRY)
TD    = "padding:9px 12px;border:1px solid #e0e0e0;vertical-align:middle"
BLOCK = "padding:12px 14px;border-radius:4px;margin-bottom:14px;line-height:1.5"
