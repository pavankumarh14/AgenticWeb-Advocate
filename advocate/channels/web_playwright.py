"""
WebChatChannel — a REAL browser channel via Playwright.  [CANDIDATE TODO / STRETCH]
===================================================================================

This is where ResolveMate actually *touches the web* — the core of the "Agentic
Web" theme. Instead of the MockChannel's role-played rep, this drives a real
browser to a company's support chat (or ticket portal), types the agent's
messages, and reads the replies back out of the DOM.

Implement it by subclassing the provided Channel interface (advocate/channels/
base.py). Keep the same three methods and the orchestrator works UNCHANGED —
just construct this channel instead of MockChannel.

Dependency (NOT installed for you — add it yourself):
    pip install playwright && playwright install chromium

Notes / tips:
  * Headful while developing (headless=False) so you can watch it work.
  * Selectors are the hard part: support widgets are often inside an <iframe>
    and lazy-load. Plan to wait for elements and to recover when they move.
  * `receive()` should POLL the DOM for a NEW counterparty bubble and return it,
    or return None after timeout_s so the orchestrator can park and resume.
  * Be a good citizen: only target sites you are authorised to test (your own
    demo support page, a sandbox, or a mock site you host). Do not hammer real
    production support systems.
"""

from typing import Optional

from .base import Channel
from ..models import Case


class WebChatChannel(Channel):
    name = "web"

    def __init__(self, start_url: str, headless: bool = False):
        # TODO(candidate): store config; lazily start Playwright in open_case so
        # importing this module never requires the dependency to be installed.
        self.start_url = start_url
        self.headless = headless
        self._page = None  # set up the browser/page on open_case

    def open_case(self, case: Case, opening_statement: str) -> None:
        """Launch the browser, navigate to the support chat, and send the opener.

        TODO(candidate):
          1. Start Playwright, launch chromium, open self.start_url.
          2. Locate and open the chat widget (handle iframes / "Start chat").
          3. Call self.send(case, opening_statement).
        """
        raise NotImplementedError("WebChatChannel.open_case: drive the browser to the support chat.")

    def send(self, case: Case, text: str) -> None:
        """Type ``text`` into the chat box and submit it.

        TODO(candidate): fill the input, press Enter / click Send, and wait for
        the outbound message to appear so you don't race the next receive().
        """
        raise NotImplementedError("WebChatChannel.send: type and submit a message in the chat widget.")

    def receive(self, case: Case, timeout_s: float = 30.0) -> Optional[str]:
        """Return the next NEW counterparty message, or None within timeout_s.

        TODO(candidate): poll the transcript DOM for a bubble newer than the last
        one you returned; extract and return its text. Return None on timeout so
        the orchestrator can park the case and resume later.
        """
        raise NotImplementedError("WebChatChannel.receive: read the newest reply bubble from the DOM.")

    def close(self) -> None:
        """TODO(candidate): tear down the page/browser/Playwright cleanly."""
        raise NotImplementedError
