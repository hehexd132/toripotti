"""
Gmail-lukija IMAP:lla.
Ei vaadi Google Cloud -konsoliasetuksia – riittää Gmail-sovelluskohtainen salasana.
"""
import email
import imaplib
import logging
from email.header import decode_header

logger = logging.getLogger(__name__)


class GmailReader:
    IMAP_SERVER = "imap.gmail.com"
    IMAP_PORT   = 993

    def __init__(self, config):
        self.config = config

    # ──────────────────────────────────────────────────────────────────────
    def fetch_unread_tori_emails(self) -> list[dict]:
        """
        Palauttaa listan dict-objekteista:
          { subject, text, html, email_id }
        Merkitsee käsitellyt emailit luetuiksi.
        """
        results = []
        try:
            mail = imaplib.IMAP4_SSL(self.IMAP_SERVER, self.IMAP_PORT)
            mail.login(self.config.imap_user, self.config.imap_password)
            mail.select("INBOX")

            # Hae kaikki lukemattomat tori.fi-emailit
            status, messages = mail.search(None, '(UNSEEN FROM "tori.fi")')

            if status != "OK" or not messages[0]:
                mail.logout()
                return []

            email_ids = messages[0].split()
            logger.info(f"IMAP: löytyi {len(email_ids)} lukematonta tori.fi-emailiä")

            for eid in email_ids:
                try:
                    status, msg_data = mail.fetch(eid, "(RFC822)")
                    if status != "OK":
                        continue

                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    # Merkitse luetuksi heti
                    mail.store(eid, "+FLAGS", "\\Seen")

                    subject      = self._decode_header(msg["Subject"])
                    text_content = ""
                    html_content = ""

                    if msg.is_multipart():
                        for part in msg.walk():
                            ctype = part.get_content_type()
                            try:
                                payload = part.get_payload(decode=True)
                                if payload is None:
                                    continue
                                decoded = payload.decode("utf-8", errors="ignore")
                            except Exception:
                                continue

                            if ctype == "text/plain" and not text_content:
                                text_content = decoded
                            elif ctype == "text/html" and not html_content:
                                html_content = decoded
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            decoded = payload.decode("utf-8", errors="ignore")
                            if msg.get_content_type() == "text/html":
                                html_content = decoded
                            else:
                                text_content = decoded

                    results.append(
                        {
                            "subject":  subject,
                            "text":     text_content,
                            "html":     html_content,
                            "email_id": eid.decode(),
                        }
                    )

                except Exception as exc:
                    logger.error(f"Virhe emailin {eid} lukemisessa: {exc}")

            mail.logout()

        except imaplib.IMAP4.error as exc:
            logger.error(f"IMAP-kirjautumisvirhe: {exc}")
        except Exception as exc:
            logger.error(f"IMAP-virhe: {exc}", exc_info=True)

        return results

    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _decode_header(header_str: str | None) -> str:
        if not header_str:
            return ""
        parts = decode_header(header_str)
        result = ""
        for part, enc in parts:
            if isinstance(part, bytes):
                result += part.decode(enc or "utf-8", errors="ignore")
            else:
                result += str(part)
        return result
