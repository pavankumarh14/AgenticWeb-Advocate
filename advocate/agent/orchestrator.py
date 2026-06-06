"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  ORCHESTRATOR  (advocate/agent/orchestrator.py)  —  IMPLEMENTED              ║
╚══════════════════════════════════════════════════════════════════════════════╝

OBJECTIVE
─────────
Build the durable, resumable resolution loop — the heart of the agent. Own
the Case state machine: plan -> open -> { receive -> classify -> decide ->
act -> persist } -> repeat, until RESOLVED / DENIED / ABANDONED, or paused
for a human. Persist after EVERY step so the polling dashboard animates live.

WHAT TO BUILD
─────────────
Two functions.

resolve_case(case_id, db_path) -> None
  1. Load the Case (store.get); stop if missing or already terminal.
  2. PLAN  — case.plan = make_plan(case, llm); status = PLANNED; save.
  3. OPEN  — channel.open_case(...); add AGENT message; status = OPEN; save.
  4. LOOP (cap at MAX_TURNS):
       reply    = channel.receive(case)      # None -> park (WAITING), return
       c        = classify(case, reply, llm) # record COUNTERPARTY msg; save
       decision = decide(c, case.policy, case.escalation_count)
       done     = _apply_decision(...)       # save after; break when done
  5. RESUME — if entered while status == NEEDS_APPROVAL, read the human's
     "User reply: ..." system message, act on approve / reject, then continue.

_apply_decision(case, decision, channel, llm, store) -> bool
Map each action to its side effects; return True to stop the loop:

  PROVIDE_INFO        →  compose & send an info / evidence message
  COUNTER_OFFER       →  compose & send a counter toward target_amount
  ESCALATE            →  send the next escalation step; escalation_count += 1
  ACCEPT              →  status RESOLVED; set outcome_kind / outcome_amount
  MARK_RESOLVED       →  status RESOLVED; record the confirmed outcome
  MARK_DENIED         →  status DENIED
  ABANDON             →  status ABANDONED
  PAUSE_FOR_APPROVAL  →  status NEEDS_APPROVAL; return True (wait for the human)

EXAMPLE SYSTEM PROMPT
─────────────────────
The LLM is used ONLY to PHRASE outbound messages — the WHICH action was
already decided by the negotiator; do not let the model second-guess it.
A starting prompt for the outbound-message composer:

    You are ResolveMate, an autonomous consumer-advocacy agent writing the next
    chat message to a company's support rep on the customer's behalf. Be
    polite, concise (1-3 sentences), firm, and factual; reference the
    evidence when useful. Output ONLY the message text — no preamble.

(make_plan and classify already own their prompts — you do not write those.)

HOW TO WIRE IT IN
─────────────────
Enable the hand-off in server.py -> run_agent():

    from advocate.agent.orchestrator import resolve_case
    resolve_case(case_id, DB_PATH)

For the human-in-the-loop pause to RESUME, also re-invoke run_agent from the
/approve handler so the case continues after the user clicks approve/reject.
Persist with store.save(case) after each step; the dashboard polls ~1.5s.

ACCEPTANCE CRITERIA
───────────────────
[ ] A newly created case runs to a terminal state with no manual steps.
[ ] The Case is persisted after every step (status advances on the dashboard).
[ ] Status flows NEW -> PLANNED -> OPEN -> ... -> RESOLVED/DENIED/ABANDONED.
[ ] NEEDS_APPROVAL pauses the loop and returns; approve/reject resumes it.
[ ] escalation_count increments on ESCALATE; the loop never exceeds MAX_TURNS.
[ ] One bad LLM/channel reply is caught and recorded, not fatal to the case.
[ ] The LLM never overrides the negotiator's chosen action (only phrases it).

IMPLEMENTATION
──────────────
The loop below plans, opens the channel, classifies replies, calls the
deterministic negotiator, persists after each step, and resumes from approval
checkpoints.
"""

from typing import Optional

from ..channels import MockChannel
from ..llm import LLMClient, LLMError
from ..models import ActionType, Case, CaseStatus, Classification, MessageRole, MessageType, OutcomeKind
from ..store import CaseStore
from .classifier import classify
from .negotiator import decide
from .strategist import make_plan


# A safety cap so a buggy loop can never run forever. Tune as needed.
MAX_TURNS = 12


def resolve_case(case_id: str, db_path: str) -> None:
    """Run (or resume) the full resolution loop for one Case. Persist every step."""
    store = CaseStore(db_path)
    try:
        case = store.get(case_id)
        if case is None or case.is_terminal():
            return

        llm = _make_llm(case)
        channel = _make_channel(case, llm)

        if case.status == CaseStatus.NEEDS_APPROVAL.value:
            if _resume_from_approval(case, channel, llm, store):
                return

        if case.plan is None:
            case.plan = make_plan(case, llm)
            case.status = CaseStatus.PLANNED.value
            case.add_message("system", "Plan created for %s." % case.plan.target_counterparty)
            store.save(case)

        if not any(m.role == MessageRole.AGENT.value for m in case.transcript):
            opening = case.plan.opening_statement if case.plan else case.goal
            channel.open_case(case, opening)
            case.add_message(MessageRole.AGENT.value, opening)
            case.status = CaseStatus.OPEN.value
            store.save(case)

        for _ in range(MAX_TURNS):
            if case.is_terminal() or case.status == CaseStatus.NEEDS_APPROVAL.value:
                return

            reply = channel.receive(case)
            if reply is None:
                case.status = CaseStatus.WAITING.value
                case.add_message("system", "No counterparty reply yet; case parked.")
                store.save(case)
                return

            classification = classify(case, reply, llm)
            case.add_message(
                MessageRole.COUNTERPARTY.value,
                reply,
                msg_type=classification.msg_type,
                meta={
                    "offer_amount": classification.offer_amount,
                    "offer_kind": classification.offer_kind,
                    "conditions": classification.conditions,
                    "is_dark_pattern": classification.is_dark_pattern,
                    "summary": classification.summary,
                },
            )
            case.status = CaseStatus.OPEN.value
            store.save(case)

            decision = decide(classification, case.policy, case.escalation_count)
            _remember_turn(case, classification, decision)
            done = _apply_decision(case, decision, channel, llm, store)
            if done:
                return

        case.status = CaseStatus.ABANDONED.value
        case.add_message("system", "Safety cap reached; abandoning case.")
        store.save(case)
    except Exception as exc:
        case = store.get(case_id)
        if case and not case.is_terminal():
            case.status = CaseStatus.WAITING.value
            case.add_message("system", _friendly_error(exc))
            store.save(case)
    finally:
        store.close()


def _apply_decision(case: Case, decision, channel, llm: LLMClient, store: CaseStore) -> bool:
    """Carry out one Decision: send messages, update status, set outcomes. Return True to stop."""
    action = decision.action
    classification = _pending_classification(case)

    case.add_message("system", "Decision: %s — %s" % (action, decision.reason))

    if action == ActionType.PROVIDE_INFO.value:
        text = _compose_message(case, decision, llm)
        channel.send(case, text)
        case.add_message(MessageRole.AGENT.value, text)
        case.status = CaseStatus.OPEN.value
        store.save(case)
        return False

    if action == ActionType.COUNTER_OFFER.value:
        text = _compose_message(case, decision, llm)
        channel.send(case, text)
        case.add_message(MessageRole.AGENT.value, text)
        case.status = CaseStatus.OPEN.value
        store.save(case)
        return False

    if action == ActionType.ESCALATE.value:
        case.escalation_count += 1
        text = _compose_message(case, decision, llm)
        channel.send(case, text)
        case.add_message(MessageRole.AGENT.value, text)
        case.status = CaseStatus.OPEN.value
        store.save(case)
        return False

    if action == ActionType.ACCEPT.value:
        _set_outcome_from_classification(case, classification)
        case.status = CaseStatus.RESOLVED.value
        case.add_message("system", "Accepted offer within policy.")
        store.save(case)
        return True

    if action == ActionType.MARK_RESOLVED.value:
        _set_outcome_from_classification(case, classification)
        case.status = CaseStatus.RESOLVED.value
        store.save(case)
        return True

    if action == ActionType.MARK_DENIED.value:
        case.status = CaseStatus.DENIED.value
        store.save(case)
        return True

    if action == ActionType.ABANDON.value:
        case.status = CaseStatus.ABANDONED.value
        store.save(case)
        return True

    if action == ActionType.PAUSE_FOR_APPROVAL.value:
        case.status = CaseStatus.NEEDS_APPROVAL.value
        store.save(case)
        return True

    case.status = CaseStatus.WAITING.value
    case.add_message("system", "Unknown action; parked for manual review.")
    store.save(case)
    return True


def _make_channel(case: Case, llm: LLMClient) -> MockChannel:
    mode = case.context.get("_counterparty_mode", "llm")
    if mode == "manual":
        return ManualChannel()
    if mode == "scripted":
        script = _default_script(case)
        seen = len([m for m in case.transcript if m.role == MessageRole.COUNTERPARTY.value])
        return MockChannel(mode="scripted", scripted_replies=script[seen:])
    return MockChannel(mode="llm", llm=llm)


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "resource_exhausted" in low or "quota" in low or "http 429" in low:
        return (
            "Agent paused because the LLM provider quota/rate limit was reached. "
            "Wait and retry, switch to Scripted or Manual support mode, or use another API key."
        )
    if "no api key" in low:
        return (
            "Agent paused because LLM role-play needs an API key. "
            "Add a key in .env or switch to Scripted or Manual support mode."
        )
    if len(text) > 220:
        text = text[:220].rstrip() + "..."
    return "Agent paused after error: %s" % text


class ManualChannel:
    name = "manual"

    def open_case(self, case: Case, opening_statement: str) -> None:
        self.send(case, opening_statement)

    def send(self, case: Case, text: str) -> None:
        pass

    def receive(self, case: Case, timeout_s: float = 0.0) -> Optional[str]:
        replies = case.context.get("_manual_replies", [])
        if not replies:
            return None
        reply = replies.pop(0)
        case.context["_manual_replies"] = replies
        return reply


def _make_llm(case: Case):
    try:
        return LLMClient()
    except LLMError as exc:
        if case.context.get("_counterparty_mode", "llm") == "llm":
            raise exc
        return _OfflineLLM()


class _OfflineLLM:
    """Tiny no-key fallback for local scripted demos."""

    def chat_json(self, messages, temperature=0.2, max_tokens=800):
        text = messages[-1]["content"]
        low = text.lower()
        if "plan the resolution" in low:
            return {
                "channel": "chat",
                "target_counterparty": "Customer Support",
                "opening_statement": "Hello, I am contacting support about this case: %s Please help resolve it under the requested policy." % _extract_goal(text),
                "anticipated_objections": [
                    "Request for proof or account details.",
                    "Offer below the requested outcome.",
                    "Retention or deflection tactic.",
                ],
                "escalation_ladder": [
                    "Restate the request and provide the available evidence.",
                    "Ask for escalation to a supervisor.",
                    "State intent to pursue a formal complaint or chargeback if applicable.",
                ],
            }

        reply = text.split("Classify ONLY this support-rep message and return the JSON:\n", 1)[-1]
        reply_low = reply.lower()
        if any(s in reply_low for s in ("cancelled", "canceled", "no further charges", "full refund", "refund of")):
            amount = _first_amount(reply_low)
            if amount:
                return {
                    "msg_type": "offer",
                    "offer_amount": amount,
                    "offer_kind": "refund",
                    "conditions": [],
                    "is_dark_pattern": False,
                    "summary": "Counterparty offered a refund.",
                }
            return {
                "msg_type": "final_resolution",
                "offer_amount": 0,
                "offer_kind": "none",
                "conditions": [],
                "is_dark_pattern": False,
                "summary": "Counterparty confirmed the requested resolution.",
            }
        if "store credit" in reply_low:
            return {
                "msg_type": "offer",
                "offer_amount": _first_amount(reply_low),
                "offer_kind": "store_credit",
                "conditions": [],
                "is_dark_pattern": False,
                "summary": "Counterparty offered store credit.",
            }
        if any(s in reply_low for s in ("discount", "stay", "before you cancel", "before you go", "reconsider")):
            return {
                "msg_type": "deflection",
                "offer_amount": _first_amount(reply_low),
                "offer_kind": "none",
                "conditions": [],
                "is_dark_pattern": True,
                "summary": "Counterparty used a retention or deflection tactic.",
            }
        if any(s in reply_low for s in ("proof", "order number", "provide", "details")):
            return {
                "msg_type": "info_request",
                "offer_amount": 0,
                "offer_kind": "none",
                "conditions": [],
                "is_dark_pattern": False,
                "summary": "Counterparty requested more information.",
            }
        if any(s in reply_low for s in ("cannot", "can't", "denied", "not eligible", "refuse")):
            return {
                "msg_type": "denial",
                "offer_amount": 0,
                "offer_kind": "none",
                "conditions": [],
                "is_dark_pattern": False,
                "summary": "Counterparty denied the request.",
            }
        return {
            "msg_type": "unknown",
            "offer_amount": 0,
            "offer_kind": "none",
            "conditions": [],
            "is_dark_pattern": False,
            "summary": "Could not classify reply offline.",
        }

    def chat(self, messages, temperature=0.3, max_tokens=800, json_mode=False):
        content = messages[-1]["content"].lower()
        if "counter_offer" in content:
            return "I cannot accept that offer under the customer's policy. Please provide the requested resolution."
        if "escalate" in content:
            return "Please escalate this to a supervisor. The customer is asking for the requested resolution based on the available evidence."
        return "Here are the requested details and available evidence. Please continue with the requested resolution."


def _extract_goal(text: str) -> str:
    marker = '"goal":'
    if marker not in text:
        return ""
    after = text.split(marker, 1)[1].strip()
    if not after.startswith('"'):
        return ""
    return after.split('"', 2)[1]


def _first_amount(text: str) -> float:
    current = ""
    for ch in text:
        if ch.isdigit() or ch == ".":
            current += ch
        elif current:
            try:
                return float(current)
            except ValueError:
                current = ""
    if current:
        try:
            return float(current)
        except ValueError:
            return 0.0
    return 0.0


def _default_script(case: Case):
    if case.policy.target_amount == 0 and case.policy.min_acceptable_amount == 0:
        return [
            "Before you cancel, we can offer you 30% off for the next three months.",
            "I understand. I can escalate this to a supervisor for immediate cancellation.",
            "Your subscription has been cancelled effective immediately and no further charges will be made.",
        ]
    target = case.policy.target_amount or case.policy.min_acceptable_amount
    return [
        "Thanks for contacting us. Could you provide the order number and proof of damage?",
        "We can offer store credit for %s %s." % (target * 0.5, case.policy.currency),
        "After review, we can issue a full refund of %s %s to the original payment method." % (
            target,
            case.policy.currency,
        ),
    ]


def _remember_turn(case: Case, classification: Classification, decision) -> None:
    case.context["_pending_classification"] = {
        "msg_type": classification.msg_type,
        "offer_amount": classification.offer_amount,
        "offer_kind": classification.offer_kind,
        "conditions": classification.conditions,
        "is_dark_pattern": classification.is_dark_pattern,
        "summary": classification.summary,
    }
    case.context["_pending_decision"] = {
        "action": decision.action,
        "reason": decision.reason,
        "target_amount": decision.target_amount,
        "requires_human": decision.requires_human,
    }


def _pending_classification(case: Case) -> Optional[Classification]:
    data = case.context.get("_pending_classification")
    if not isinstance(data, dict):
        return None
    return Classification(
        msg_type=data.get("msg_type", MessageType.UNKNOWN.value),
        offer_amount=float(data.get("offer_amount", 0) or 0),
        offer_kind=data.get("offer_kind", OutcomeKind.NONE.value),
        conditions=list(data.get("conditions", []) or []),
        is_dark_pattern=bool(data.get("is_dark_pattern", False)),
        summary=data.get("summary", ""),
    )


def _set_outcome_from_classification(case: Case, classification: Optional[Classification]) -> None:
    if classification is None:
        case.outcome_kind = OutcomeKind.NONE.value
        case.outcome_amount = 0.0
        return
    if classification.offer_kind != OutcomeKind.NONE.value:
        case.outcome_kind = classification.offer_kind
    elif case.policy.target_amount == 0 and case.policy.min_acceptable_amount == 0:
        case.outcome_kind = OutcomeKind.NONE.value
    case.outcome_amount = float(classification.offer_amount or 0)


def _resume_from_approval(case: Case, channel, llm: LLMClient, store: CaseStore) -> bool:
    reply = _latest_user_reply(case)
    if reply is None:
        return True

    approved = bool(reply.get("approved"))
    if approved:
        classification = _pending_classification(case)
        _set_outcome_from_classification(case, classification)
        case.status = CaseStatus.RESOLVED.value
        case.add_message("system", "User approved the pending resolution.")
        store.save(case)
        return True

    case.status = CaseStatus.OPEN.value
    text = reply.get("note") or "The user rejected this offer. Please proceed with the requested resolution."
    outbound = _compose_message(
        case,
        type("DecisionLike", (), {
            "action": ActionType.COUNTER_OFFER.value,
            "reason": text,
            "target_amount": case.policy.target_amount,
        })(),
        llm,
    )
    channel.send(case, outbound)
    case.add_message(MessageRole.AGENT.value, outbound)
    store.save(case)
    return False


def _latest_user_reply(case: Case) -> Optional[dict]:
    for message in reversed(case.transcript):
        if message.role == MessageRole.SYSTEM.value and message.meta.get("kind") == "user_reply":
            return message.meta
    return None


def _compose_message(case: Case, decision, llm: LLMClient) -> str:
    fallback = _fallback_message(case, decision)
    if decision.action in (
        ActionType.PROVIDE_INFO.value,
        ActionType.COUNTER_OFFER.value,
        ActionType.ESCALATE.value,
    ):
        return fallback
    try:
        transcript = [
            {"role": m.role, "text": m.text, "type": m.msg_type}
            for m in case.transcript[-6:]
        ]
        prompt = {
            "goal": case.goal,
            "policy": case.policy.__dict__,
            "decision": {
                "action": decision.action,
                "reason": decision.reason,
                "target_amount": getattr(decision, "target_amount", 0),
            },
            "escalation_count": case.escalation_count,
            "escalation_ladder": case.plan.escalation_ladder if case.plan else [],
            "recent_transcript": transcript,
        }
        generated = llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are ResolveMate, an autonomous consumer-advocacy agent writing the next "
                        "chat message to a company's support rep on the customer's behalf. Be polite, "
                        "concise, firm, and factual. The action is already decided; do not change it. "
                        "Output only the message text."
                    ),
                },
                {"role": "user", "content": str(prompt)},
            ],
            temperature=0.25,
            max_tokens=220,
        ).strip()
        if _looks_bad_agent_message(generated):
            return fallback
        return generated
    except Exception:
        return fallback


def _looks_bad_agent_message(text: str) -> bool:
    cleaned = " ".join((text or "").split())
    low = cleaned.lower()
    if len(cleaned) < 45:
        return True
    bad_starts = (
        "hello! it seems your message",
        "hello it seems your message",
        "i understand you're",
        "it seems your message",
    )
    if any(low.startswith(start) for start in bad_starts):
        return True
    if low.endswith(("you're", "you are", "your", "the", "a", "an", "and", "or", "but", "to")):
        return True
    return False


def _fallback_message(case: Case, decision) -> str:
    action = decision.action
    is_cancellation = case.policy.target_amount == 0 and case.policy.min_acceptable_amount == 0
    if action == ActionType.PROVIDE_INFO.value:
        details = _case_details(case)
        evidence = ", ".join(case.evidence) if case.evidence else ""
        if "unclear" in (decision.reason or "").lower() or "clarification" in (decision.reason or "").lower():
            return (
                "I want to make sure I understand your reply. Please confirm whether you are requesting "
                "more information, denying the request, or making a concrete offer."
            )
        if is_cancellation:
            return (
                "Here are the verification details: %s. Please proceed with immediate cancellation "
                "and confirm that no further charges will be applied."
                % details
            )
        return (
            "Here are the requested details%s: %s. Please continue with the requested resolution."
            % ((" and evidence" if evidence else ""), details + (("; evidence: " + evidence) if evidence else ""))
        )
    if action == ActionType.COUNTER_OFFER.value:
        if is_cancellation:
            return (
                "I cannot accept retention offers or store credit for this request. Please cancel the subscription now "
                "and confirm in writing that no further charges will be applied."
            )
        return (
            "I cannot accept that offer under the customer's policy. Please provide the requested resolution"
            " of %s %s."
            % (decision.target_amount or case.policy.target_amount, case.policy.currency)
        )
    if action == ActionType.ESCALATE.value:
        if is_cancellation:
            return (
                "Please escalate this cancellation request to a supervisor. The customer has already asked to cancel "
                "and is not interested in retention discounts; please confirm cancellation and no further billing."
            )
        ladder = case.plan.escalation_ladder if case.plan else []
        step = ladder[min(case.escalation_count, len(ladder) - 1)] if ladder else "Please escalate this to a supervisor."
        return "%s The customer is asking for: %s" % (step, case.goal)
    return "Please proceed with the requested resolution: %s" % case.goal


def _case_details(case: Case) -> str:
    parts = []
    for key, label in (
        ("order_id", "reference"),
        ("account_email", "account email"),
        ("item", "item"),
        ("plan", "plan"),
        ("renewal_date", "renewal date"),
    ):
        value = case.context.get(key)
        if value:
            parts.append("%s: %s" % (label, value))
    return "; ".join(parts) if parts else "the account details already provided"
