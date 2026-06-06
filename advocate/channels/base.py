"""
Channel interface — the "sense & act" boundary.
===============================================

A Channel is how ResolveMate talks to the outside world and receives replies.
The orchestrator only ever uses this small interface, so any real channel
(Playwright web chat, ticket portal, email via IMAP/SMTP, SMS, ...) can be
dropped in without touching the reasoning loop.

>>> CANDIDATES: implement real channels by subclassing `Channel`. See
    channels/mock.py for a working reference implementation and the README
    "What's left to build" section.
"""

from abc import ABC, abstractmethod
from typing import Optional

from ..models import Case


class Channel(ABC):
    """Abstract send/receive transport for a Case."""

    name = "base"

    @abstractmethod
    def open_case(self, case: Case, opening_statement: str) -> None:
        """Open the conversation (e.g. open the chat widget / create a ticket /
        send the first email) and deliver the opening statement + evidence."""
        raise NotImplementedError

    @abstractmethod
    def send(self, case: Case, text: str) -> None:
        """Send one outbound message on the channel."""
        raise NotImplementedError

    @abstractmethod
    def receive(self, case: Case, timeout_s: float = 0.0) -> Optional[str]:
        """Return the next inbound counterparty message, or None if none has
        arrived within ``timeout_s``.

        In the real, durable system this maps to *parking on a durable wait*
        and being woken by an inbound signal (email via Event Grid, a portal
        status poll, or a new chat message). In this prototype the MockChannel
        returns synchronously."""
        raise NotImplementedError
