"""
Classifier — interpret one inbound counterparty reply.  [PROVIDED]
==================================================================

Input:  the Case (for context / running transcript) + the raw reply text.
Output: a Classification — what KIND of message it is and the structured facts
        the negotiator needs to make a safe decision.

This is the agent's "perception". It only *describes* the message; it never
decides what to do about it. The consequential decision is made by the
deterministic negotiator (``negotiator.py``) that YOU build.

>>> This file is part of the provided foundation. You can use it as-is, tune the
    prompt, or replace it entirely. The orchestrator you build calls
    ``classify(case, reply_text, llm)`` and expects a Classification back.

Provided building blocks:
    from ..llm import LLMClient
    from ..models import Case, Classification, MessageType, OutcomeKind
"""

import json
from typing import Any, List

from ..llm import LLMClient, LLMError
from ..models import Case, Classification, MessageRole, MessageType, OutcomeKind


CLASSIFIER_SYSTEM = """\
You read ONE message from a company's support rep and extract structured facts
for a downstream negotiation engine. You do not decide what to do — you only
classify and extract. Be strict and literal; do not invent an offer that wasn't
made.

Respond with ONLY a JSON object (no prose, no markdown fences) with EXACTLY
these keys:
{
  "msg_type": one of ["offer","info_request","deflection","denial",
                      "final_resolution","unknown"],
  "offer_amount": number,        // concrete monetary amount offered, else 0
  "offer_kind": one of ["refund","replacement","store_credit","apology","none"],
  "conditions": [string, ...],   // strings attached to the offer, if any
  "is_dark_pattern": boolean,    // retention/deflection manipulation present?
  "summary": string              // one short plain-English sentence
}

Guidance:
- "offer"            -> a concrete resolution is proposed (money, replacement,
                       store credit, etc). Fill offer_amount / offer_kind.
- "info_request"     -> they need more details/proof from us before acting.
- "deflection"       -> stalling, "let me check", or a RETENTION tactic such as
                       "stay for a discount" / "are you sure you want to cancel?"
                       Set is_dark_pattern=true for retention/manipulative stalls.
- "denial"           -> they refuse to resolve.
- "final_resolution" -> they confirm the matter is settled/closed on their side
                       (e.g. refund issued, subscription cancelled confirmed).
- "unknown"          -> none of the above / unparseable.
offer_amount is a plain number with no currency symbol. If no money is offered,
use 0 and offer_kind accordingly.
"""


def classify(case: Case, reply_text: str, llm: LLMClient) -> Classification:
    """Classify a single inbound reply into a Classification.

    Sends the reply (plus light context: the goal and the agent's last message)
    to the LLM, then coerces the result defensively so a junk reply can never
    feed bad data into the negotiator.
    """
    messages = [
        {"role": "system", "content": CLASSIFIER_SYSTEM},
        {
            "role": "user",
            "content": (
                "Context for interpretation (background only, do not classify it):\n"
                + json.dumps(
                    {
                        "customer_goal": case.goal,
                        "agent_last_said": _last_agent_message(case),
                    },
                    ensure_ascii=False,
                )
                + "\n\nClassify ONLY this support-rep message and return the JSON:\n"
                + reply_text
            ),
        },
    ]

    try:
        data = llm.chat_json(messages)
    except LLMError:
        # If perception fails, return a safe UNKNOWN so the negotiator asks to
        # clarify rather than acting on garbage.
        return Classification(
            msg_type=MessageType.UNKNOWN.value,
            summary="Could not interpret the reply (classifier error).",
        )

    msg_type = _clean_enum(data.get("msg_type"), _MSG_TYPES, MessageType.UNKNOWN.value)
    offer_kind = _clean_enum(data.get("offer_kind"), _OUTCOME_KINDS, OutcomeKind.NONE.value)
    is_dark_pattern = bool(data.get("is_dark_pattern", False))

    # Belt-and-braces: catch obvious retention dark-patterns the model may miss.
    if _looks_like_retention(reply_text):
        is_dark_pattern = True
        if msg_type == MessageType.UNKNOWN.value:
            msg_type = MessageType.DEFLECTION.value

    return Classification(
        msg_type=msg_type,
        offer_amount=_clean_amount(data.get("offer_amount")),
        offer_kind=offer_kind,
        conditions=_clean_list(data.get("conditions")),
        is_dark_pattern=is_dark_pattern,
        summary=_clean_str(data.get("summary")),
    )


# ---------------------------------------------------------------------------
# Defensive helpers
# ---------------------------------------------------------------------------

_MSG_TYPES = {t.value for t in MessageType}
_OUTCOME_KINDS = {k.value for k in OutcomeKind}

_RETENTION_HINTS = (
    "are you sure",
    "discount",
    "stay with us",
    "special offer",
    "before you go",
    "reconsider",
    "we'd hate to see you go",
    "we would hate to see you go",
    "keep your subscription",
)


def _looks_like_retention(text: str) -> bool:
    low = text.lower()
    return any(hint in low for hint in _RETENTION_HINTS)


def _clean_enum(value: Any, allowed: set, default: str) -> str:
    if isinstance(value, str) and value.strip().lower() in allowed:
        return value.strip().lower()
    return default


def _clean_amount(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    if isinstance(value, str):
        # Strip currency symbols / commas / spaces, keep digits and dot.
        cleaned = "".join(ch for ch in value if ch.isdigit() or ch == ".")
        try:
            return max(0.0, float(cleaned)) if cleaned else 0.0
        except ValueError:
            return 0.0
    return 0.0


def _clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _last_agent_message(case: Case) -> str:
    for m in reversed(case.transcript):
        if m.role == MessageRole.AGENT.value:
            return m.text
    return ""
