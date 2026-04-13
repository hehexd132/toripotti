"""
Konfiguraatio – luetaan GitHub Secrets -ympäristömuuttujista.
Paikallistestauksessa voit luoda .env-tiedoston ja ajaa:
  export $(cat .env | xargs) && python main.py
"""
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # ── Gmail IMAP (lukeminen) ─────────────────────────────────────────────
    imap_user: str = field(default="")
    imap_password: str = field(default="")   # Gmail-sovelluskohtainen salasana

    # ── SMTP (hälytysten lähetys) ──────────────────────────────────────────
    smtp_user: str = field(default="")
    smtp_password: str = field(default="")

    # ── Hälytyskohteet ────────────────────────────────────────────────────
    alert_to: str = field(default="")        # Oma henkilökohtainen sähköpostisi

    # ── Anthropic (Claude Haiku) ───────────────────────────────────────────
    anthropic_api_key: str = field(default="")

    # ── Hälytyskynnykseet ─────────────────────────────────────────────────
    min_profit_pct: float = field(default=50.0)   # Minimivoittoprosentti
    min_profit_eur: float = field(default=15.0)   # Minimieuro-voitto (suodattaa halppikset)

    def __post_init__(self):
        self.imap_user        = os.environ.get("IMAP_USER", "")
        self.imap_password    = os.environ.get("IMAP_PASSWORD", "")
        self.smtp_user        = os.environ.get("SMTP_USER", self.imap_user)
        self.smtp_password    = os.environ.get("SMTP_PASSWORD", self.imap_password)
        self.alert_to         = os.environ.get("ALERT_TO", "")
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        # Kynnykset voidaan ylikirjoittaa ympäristömuuttujilla
        self.min_profit_pct = float(os.environ.get("MIN_PROFIT_PCT", self.min_profit_pct))
        self.min_profit_eur = float(os.environ.get("MIN_PROFIT_EUR", self.min_profit_eur))

        missing = [
            name for name, val in {
                "IMAP_USER": self.imap_user,
                "IMAP_PASSWORD": self.imap_password,
                "ALERT_TO": self.alert_to,
                "ANTHROPIC_API_KEY": self.anthropic_api_key,
            }.items() if not val
        ]
        if missing:
            raise ValueError(f"Puuttuvat ympäristömuuttujat: {', '.join(missing)}")
