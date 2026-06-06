"""
ResolveMate agent package — THE PART YOU BUILD (~50%).
===================================================

This package is intentionally a SKELETON. The data models, persistence, LLM
client, channel interface and dashboard are already provided and working (the
~50% foundation). The *intelligence* — planning, interpreting replies, deciding
the next action under the user's policy, and the loop that ties it together —
lives here and is yours to implement.

Suggested decomposition (you may restructure freely):

    strategist.py   -> turn (goal + policy) into a CasePlan                (LLM)
    classifier.py   -> turn one inbound reply into a Classification        (LLM)
    negotiator.py   -> turn (Classification + policy) into a Decision   (CODE)
    orchestrator.py -> the durable resolution loop that drives the above

The deliberate design seam: let the LLM *understand and phrase* (strategist,
classifier), but keep the *consequential decision* (accept money? cross a
guardrail?) in deterministic code (negotiator) so a model mistake can never
silently approve a payment.

Nothing here is wired into `server.py` until you implement
`orchestrator.resolve_case` and call it from `server.run_agent`.
"""
