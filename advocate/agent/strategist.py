"""
Strategist — plan a resolution strategy.  [PROVIDED]
====================================================

Input:  a Case (goal, context, evidence) + its ResolutionPolicy.
Output: a CasePlan (channel, target counterparty, opening statement,
        anticipated objections, escalation ladder).

This is an LLM job: it *understands and phrases*. It does NOT make the
consequential accept/reject decision — that belongs to the negotiator
(``negotiator.py``), which is the deterministic trust boundary YOU build.

>>> This file is part of the provided foundation. You can use it as-is, tune the
    prompt, or replace it entirely. The orchestrator you build calls
    ``make_plan(case, llm)`` and expects a fully-populated CasePlan back.

Provided building blocks used here:
    from ..llm import LLMClient            # .chat() / .chat_json()
    from ..models import Case, CasePlan
"""

import json
from typing import Any, List

from ..llm import LLMClient, LLMError
from ..models import Case, CasePlan


# The model is told exactly which JSON to return so the reply maps 1:1 onto
# CasePlan. Tune this freely — prompt quality directly shapes the negotiation.
STRATEGIST_SYSTEM = """\
You are the Strategist for an autonomous consumer-advocacy agent named ResolveMate.
Given a user's desired outcome and their resolution policy, produce a concrete,
firm-but-polite plan to win that outcome from a company's support channel.

Think like a seasoned consumer-rights negotiator:
- Open by stating the facts and the specific outcome wanted, citing evidence.
- Anticipate the usual stalling tactics (asking for proof already provided,
  offering store credit instead of a cash refund, "are you sure you want to
  cancel?" retention offers) and plan around them.
- Build an escalation ladder of progressively firmer moves (restate with
  evidence -> cite consumer-protection norms / policy -> ask for a supervisor
  -> state intent to pursue a chargeback / formal complaint). Never threaten
  anything unlawful; stay factual and professional.

Respond with ONLY a JSON object (no prose, no markdown fences) with EXACTLY
these keys:
{
  "channel": "chat" | "email" | "portal",   // where to pursue this
  "target_counterparty": string,            // who we're addressing, e.g. "Acme Support"
  "opening_statement": string,              // the first message to send; concrete,
                                            //   states the goal + cites evidence
  "anticipated_objections": [string, ...],  // likely pushback we expect
  "escalation_ladder": [string, ...]        // ordered firmer moves if stalled
}
Keep the opening_statement to a few sentences — it must read like a real first
chat/email message, not a summary.
"""


def make_plan(case: Case, llm: LLMClient) -> CasePlan:
    """Produce a CasePlan for ``case`` using the LLM.

    Builds a planning prompt from the case goal/context/evidence/policy, asks the
    LLM for JSON, and maps it defensively onto a CasePlan so the loop never
    crashes on a malformed model reply.
    """
    policy = case.policy
    user_payload = {
        "goal": case.goal,
        "context": case.context,
        "evidence": case.evidence,
        "policy": {
            "currency": policy.currency,
            "target_amount": policy.target_amount,
            "min_acceptable_amount": policy.min_acceptable_amount,
            "forbidden_outcomes": policy.forbidden_outcomes,
            "escalation_budget": policy.escalation_budget,
            "notes": policy.notes,
        },
    }
    messages = [
        {"role": "system", "content": STRATEGIST_SYSTEM},
        {
            "role": "user",
            "content": (
                "Plan the resolution for this case. Return only the JSON object.\n\n"
                + json.dumps(user_payload, ensure_ascii=False, indent=2)
            ),
        },
    ]

    try:
        data = llm.chat_json(messages)
    except LLMError:
        # Never let a bad model reply crash the loop — fall back to a usable plan
        # built directly from the case so the agent can still open and negotiate.
        return _fallback_plan(case)

    return CasePlan(
        channel=_clean_channel(data.get("channel")),
        target_counterparty=_clean_str(data.get("target_counterparty"))
        or "Customer Support",
        opening_statement=_clean_str(data.get("opening_statement"))
        or _default_opening(case),
        anticipated_objections=_clean_list(data.get("anticipated_objections")),
        escalation_ladder=_clean_list(data.get("escalation_ladder"))
        or _default_ladder(),
    )


# ---------------------------------------------------------------------------
# Defensive helpers — coerce whatever the model returned into clean values.
# ---------------------------------------------------------------------------

_VALID_CHANNELS = {"chat", "email", "portal"}


def _clean_channel(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in _VALID_CHANNELS:
        return value.strip().lower()
    return "chat"


def _clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


# ---------------------------------------------------------------------------
# Fallbacks — keep the agent working even if the LLM is unavailable / off-spec.
# ---------------------------------------------------------------------------

def _default_opening(case: Case) -> str:
    return (
        "Hello, I'm reaching out on behalf of the customer regarding: "
        + case.goal.strip()
        + " Supporting evidence is available. Please let me know how we can "
        "resolve this."
    )


def _default_ladder() -> List[str]:
    return [
        "Restate the request firmly and re-share the evidence.",
        "Cite the company's stated policy and consumer-protection norms.",
        "Request escalation to a supervisor.",
        "State intent to pursue a formal complaint / chargeback.",
    ]


def _fallback_plan(case: Case) -> CasePlan:
    return CasePlan(
        channel="chat",
        target_counterparty="Customer Support",
        opening_statement=_default_opening(case),
        anticipated_objections=[
            "Request for proof already provided.",
            "Offer of store credit instead of a refund.",
            "Retention discount / 'are you sure?' deflection.",
        ],
        escalation_ladder=_default_ladder(),
    )
