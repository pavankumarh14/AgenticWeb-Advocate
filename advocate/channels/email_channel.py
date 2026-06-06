"""
EmailChannel — a REAL email channel via IMAP/SMTP.  [CANDIDATE TODO / STRETCH]
==============================================================================

The other realistic way ResolveMate gets things done across services: open a case
by EMAILING support, then poll the inbox for the reply. This is also the natural
home for the "true multi-day durability" stretch goal — an email thread can span
days, so `receive()` returning None (no reply yet) lets the orchestrator park the
case and resume when a reply lands.

Stdlib-only is possible here (smtplib + imaplib + email), so no new dependency is
strictly required.

Tips:
  * Use a throwaway / test mailbox with an app password — never a personal one.
  * Thread the conversation: keep a stable Subject and set In-Reply-To / your own
    case-id token so you can match the right replies.
  * `receive()` should fetch UNSEEN messages matching this case's thread, return
    the newest body text, and mark it seen; return None if nothing new yet.
"""

from typing import Optional

from .base import Channel
from ..models import Case


class EmailChannel(Channel):
    name = "email"

    def __init__(self, smtp_host: str, imap_host: str, address: str, password: str,
                 to_address: str):
        # TODO(candidate): store connection config. Prefer reading secrets from
        # environment variables / .env rather than hard-coding them.
        self.smtp_host = smtp_host
        self.imap_host = imap_host
        self.address = address
        self.password = password
        self.to_address = to_address

    def open_case(self, case: Case, opening_statement: str) -> None:
        """Send the first email stating the case (subject carries a case token).

        TODO(candidate): compose a MIME message (subject includes case.case_id so
        replies can be matched), then self.send(case, opening_statement) or send
        directly via smtplib.
        """
        raise NotImplementedError("EmailChannel.open_case: send the opening email via SMTP.")

    def send(self, case: Case, text: str) -> None:
        """Send a reply in the case's email thread.

        TODO(candidate): build a message that References / In-Reply-To the thread
        and deliver it over SMTP.
        """
        raise NotImplementedError("EmailChannel.send: send a threaded reply via SMTP.")

    def receive(self, case: Case, timeout_s: float = 0.0) -> Optional[str]:
        """Return the newest unread reply in this case's thread, or None.

        TODO(candidate): connect via IMAP, search for UNSEEN messages matching
        this case's thread/token, return the newest body text (and mark it seen).
        Returning None signals "no reply yet" so the case can park and resume.
        """
        raise NotImplementedError("EmailChannel.receive: poll IMAP for the next reply in this thread.")
