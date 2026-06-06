"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  NEGOTIATOR  (advocate/agent/negotiator.py)  —  IMPLEMENTED                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

OBJECTIVE
─────────
Implement the deterministic decision engine — the agent's TRUST BOUNDARY.
Turn one Classification (what the counterparty said), the user's
ResolutionPolicy (their mandate), and how many times we have already
escalated into the single next action — in plain, auditable Python. Because
a model can be wrong, the irreversible choice (accept money, cross a
"never" rule, give up) is made HERE, in code you can read and test.

WHAT TO BUILD
─────────────
Function:  decide(classification, policy, escalation_count) -> Decision

Map each inbound message type to exactly one action:

  FINAL_RESOLUTION           →  MARK_RESOLVED
  INFO_REQUEST               →  PROVIDE_INFO
  DENIAL                     →  ESCALATE while budget remains, else MARK_DENIED
  DEFLECTION / dark-pattern  →  ESCALATE while budget remains, else PAUSE_FOR_APPROVAL
  UNKNOWN                    →  PROVIDE_INFO (ask to clarify)

For an OFFER, decide in this order:

    kind in forbidden_outcomes       →  COUNTER_OFFER (never accept)
    amount >= min_acceptable_amount  →  ACCEPT
    below + ask_below_threshold      →  PAUSE_FOR_APPROVAL
    below, otherwise                 →  COUNTER_OFFER (toward target_amount)

A CANCELLATION goal (target_amount == 0 and min_acceptable_amount == 0) is a
special case: a money / discount OFFER is retention BAIT, not a win — the
win is written confirmation (FINAL_RESOLUTION). Never accept money for it;
escalate, or pause once the budget is spent.

Fill the returned Decision:

  .reason          —  short audit-trail justification (shown in the transcript)
  .target_amount   —  the amount to aim for on a COUNTER_OFFER
  .requires_human  —  set True for any PAUSE_FOR_APPROVAL

EXAMPLE SYSTEM PROMPT
─────────────────────
Not applicable — THIS MODULE MUST NOT CALL AN LLM. Determinism is the whole
point: a model mistake must never be able to approve a payment or break a
"never" rule. No prompt, no network call, no randomness in this file.

HOW TO WIRE IT IN
─────────────────
The orchestrator calls it once per turn (it is already imported there):

    from .negotiator import decide
    decision = decide(classification, case.policy, case.escalation_count)

You only implement the body of decide() below.

ACCEPTANCE CRITERIA
───────────────────
[ ] Pure function: no I/O, no LLM, no randomness — same inputs, same Decision.
[ ] NEVER returns ACCEPT when offer_kind is in policy.forbidden_outcomes.
[ ] NEVER returns ACCEPT for an amount below min_acceptable_amount.
[ ] Escalates only while escalation_count < escalation_budget; else pause/deny.
[ ] A cancellation goal never accepts a money / discount offer.
[ ] Returns a valid Decision for EVERY MessageType, including UNKNOWN.
[ ] requires_human is True for every PAUSE_FOR_APPROVAL.
[ ] Directly unit-testable (reviewers probe this hardest).

IMPLEMENTATION
──────────────
The function below implements these rules directly in deterministic Python.
"""

from ..models import (
    ActionType,
    Classification,
    Decision,
    MessageType,
    OutcomeKind,
    ResolutionPolicy,
)


def decide(
    classification: Classification,
    policy: ResolutionPolicy,
    escalation_count: int,
) -> Decision:
    """Choose the next action under the policy. Pure function — no I/O, no LLM."""
    msg_type = (classification.msg_type or MessageType.UNKNOWN.value).lower()
    offer_kind = (classification.offer_kind or OutcomeKind.NONE.value).lower()
    forbidden = {kind.lower() for kind in policy.forbidden_outcomes}
    budget_remaining = escalation_count < policy.escalation_budget
    is_cancellation_goal = (
        float(policy.target_amount or 0) == 0.0
        and float(policy.min_acceptable_amount or 0) == 0.0
    )

    if msg_type == MessageType.FINAL_RESOLUTION.value:
        return Decision(
            action=ActionType.MARK_RESOLVED.value,
            reason="Counterparty confirmed the requested resolution.",
        )

    if msg_type == MessageType.INFO_REQUEST.value:
        return Decision(
            action=ActionType.PROVIDE_INFO.value,
            reason="Counterparty requested more information or evidence.",
        )

    if msg_type == MessageType.DENIAL.value:
        if budget_remaining:
            return Decision(
                action=ActionType.ESCALATE.value,
                reason="Counterparty denied the request; escalation budget remains.",
            )
        return Decision(
            action=ActionType.MARK_DENIED.value,
            reason="Counterparty denied the request and escalation budget is exhausted.",
        )

    if msg_type == MessageType.DEFLECTION.value or classification.is_dark_pattern:
        if budget_remaining:
            return Decision(
                action=ActionType.ESCALATE.value,
                reason="Counterparty deflected or used a dark pattern; escalation budget remains.",
            )
        return Decision(
            action=ActionType.PAUSE_FOR_APPROVAL.value,
            reason="Counterparty deflected after escalation budget was exhausted.",
            requires_human=True,
        )

    if msg_type == MessageType.OFFER.value:
        # Cancellation goals are won by written confirmation, not retention money.
        if is_cancellation_goal and (
            classification.offer_amount > 0
            or offer_kind in {OutcomeKind.REFUND.value, OutcomeKind.STORE_CREDIT.value}
        ):
            if budget_remaining:
                return Decision(
                    action=ActionType.ESCALATE.value,
                    reason="Money or credit offer looks like retention bait for a cancellation goal.",
                )
            return Decision(
                action=ActionType.PAUSE_FOR_APPROVAL.value,
                reason="Retention-style offer received after escalation budget was exhausted.",
                requires_human=True,
            )

        if offer_kind in forbidden:
            return Decision(
                action=ActionType.COUNTER_OFFER.value,
                reason="Offer kind is forbidden by the user's policy.",
                target_amount=float(policy.target_amount or policy.min_acceptable_amount or 0),
            )

        if classification.offer_amount >= float(policy.min_acceptable_amount or 0):
            return Decision(
                action=ActionType.ACCEPT.value,
                reason="Offer meets the minimum acceptable amount and is not forbidden.",
            )

        if policy.ask_below_threshold:
            return Decision(
                action=ActionType.PAUSE_FOR_APPROVAL.value,
                reason="Offer is below the user's minimum acceptable amount.",
                requires_human=True,
            )

        return Decision(
            action=ActionType.COUNTER_OFFER.value,
            reason="Offer is below threshold; countering toward the target amount.",
            target_amount=float(policy.target_amount or policy.min_acceptable_amount or 0),
        )

    return Decision(
        action=ActionType.PROVIDE_INFO.value,
        reason="Reply was unclear; ask for clarification.",
    )
