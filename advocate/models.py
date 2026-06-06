"""
Core data models for a Case.
============================

A *Case* is the unit of work: one outcome the user wants, pursued across one or
more channels over time. Everything is a plain dataclass that serialises to/from
JSON so it can be persisted (see store.py) — this is the simplified, local stand-in
for what the problem statement stores in Cosmos DB.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional
import time
import uuid


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CaseStatus(str, Enum):
    NEW = "new"                     # declared, not yet planned
    PLANNED = "planned"             # strategist produced a plan
    OPEN = "open"                   # opening message sent, awaiting reply
    WAITING = "waiting"             # parked, waiting for an inbound signal
    NEEDS_APPROVAL = "needs_approval"  # human-in-the-loop checkpoint
    RESOLVED = "resolved"           # goal met
    DENIED = "denied"               # documented final denial
    ABANDONED = "abandoned"         # escalation budget exhausted, no resolution


class MessageRole(str, Enum):
    AGENT = "agent"                 # ResolveMate -> counterparty
    COUNTERPARTY = "counterparty"   # counterparty -> ResolveMate
    SYSTEM = "system"               # internal notes / events


class MessageType(str, Enum):
    """How the classifier categorises an inbound counterparty message."""
    OFFER = "offer"                       # a concrete resolution offer
    INFO_REQUEST = "info_request"         # they need more details from us
    DEFLECTION = "deflection"             # stalling / retention dark-pattern
    DENIAL = "denial"                     # they refuse
    FINAL_RESOLUTION = "final_resolution" # accepted/closed on their side
    UNKNOWN = "unknown"


class ActionType(str, Enum):
    """What the negotiation engine decides to do next."""
    PROVIDE_INFO = "provide_info"
    COUNTER_OFFER = "counter_offer"
    ACCEPT = "accept"
    ESCALATE = "escalate"
    PAUSE_FOR_APPROVAL = "pause_for_approval"
    MARK_RESOLVED = "mark_resolved"
    MARK_DENIED = "mark_denied"
    ABANDON = "abandon"


class OutcomeKind(str, Enum):
    REFUND = "refund"
    REPLACEMENT = "replacement"
    STORE_CREDIT = "store_credit"
    APOLOGY = "apology"
    NONE = "none"


# ---------------------------------------------------------------------------
# Policy — the user's mandate the agent operates within
# ---------------------------------------------------------------------------

@dataclass
class ResolutionPolicy:
    """The guardrails. The negotiation engine never crosses these."""
    currency: str = "INR"
    # Accept any acceptable-kind offer at or above this amount automatically.
    min_acceptable_amount: float = 0.0
    # What we're aiming for; used when composing counter-offers.
    target_amount: float = 0.0
    # Outcome kinds we will NEVER accept (e.g. "store_credit").
    forbidden_outcomes: List[str] = field(default_factory=list)
    # If an offer is below min_acceptable_amount: True -> ask the user;
    # False -> automatically counter-offer.
    ask_below_threshold: bool = True
    # How many times the agent may escalate before giving up / asking the user.
    escalation_budget: int = 2
    # Free-text extra instructions surfaced to the LLM agents.
    notes: str = ""

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ResolutionPolicy":
        known = {f for f in ResolutionPolicy.__dataclass_fields__}  # type: ignore[attr-defined]
        return ResolutionPolicy(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Messages and Plans
# ---------------------------------------------------------------------------

@dataclass
class Message:
    role: str                      # MessageRole value
    text: str
    ts: float = field(default_factory=time.time)
    msg_type: Optional[str] = None  # MessageType value (set for counterparty msgs)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CasePlan:
    """Output of the Strategist agent."""
    channel: str = "chat"          # chat | email | portal
    target_counterparty: str = ""
    opening_statement: str = ""
    anticipated_objections: List[str] = field(default_factory=list)
    escalation_ladder: List[str] = field(default_factory=list)


@dataclass
class Classification:
    """Output of the Classifier agent for one inbound message."""
    msg_type: str = MessageType.UNKNOWN.value
    offer_amount: float = 0.0
    offer_kind: str = OutcomeKind.NONE.value
    conditions: List[str] = field(default_factory=list)
    is_dark_pattern: bool = False
    summary: str = ""


@dataclass
class Decision:
    """Output of the (deterministic) negotiation engine."""
    action: str                    # ActionType value
    reason: str = ""
    target_amount: float = 0.0     # for counter offers
    requires_human: bool = False


# ---------------------------------------------------------------------------
# Case — the whole record
# ---------------------------------------------------------------------------

@dataclass
class Case:
    goal: str
    policy: ResolutionPolicy
    context: Dict[str, Any] = field(default_factory=dict)   # order id, account email...
    evidence: List[str] = field(default_factory=list)        # paths / descriptions
    case_id: str = field(default_factory=lambda: "case_" + uuid.uuid4().hex[:10])
    status: str = CaseStatus.NEW.value
    plan: Optional[CasePlan] = None
    transcript: List[Message] = field(default_factory=list)
    escalation_count: int = 0
    outcome_kind: str = OutcomeKind.NONE.value
    outcome_amount: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # -- convenience --------------------------------------------------------

    def add_message(self, role: str, text: str, msg_type: Optional[str] = None,
                    meta: Optional[Dict[str, Any]] = None) -> Message:
        m = Message(role=role, text=text, msg_type=msg_type, meta=meta or {})
        self.transcript.append(m)
        self.updated_at = time.time()
        return m

    def is_terminal(self) -> bool:
        return self.status in (
            CaseStatus.RESOLVED.value,
            CaseStatus.DENIED.value,
            CaseStatus.ABANDONED.value,
        )

    # -- (de)serialisation --------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Case":
        d = dict(d)
        d["policy"] = ResolutionPolicy.from_dict(d.get("policy", {}))
        if d.get("plan"):
            d["plan"] = CasePlan(**d["plan"])
        d["transcript"] = [Message(**m) for m in d.get("transcript", [])]
        known = {f for f in Case.__dataclass_fields__}  # type: ignore[attr-defined]
        return Case(**{k: v for k, v in d.items() if k in known})
