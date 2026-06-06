#!/usr/bin/env python3
"""
ResolveMate — web server SHELL (provided, working).
================================================

This is part of the working foundation. It serves the dashboard and a small
storage-backed API (stdlib http.server only — no third-party deps). It runs
as-is: you can open the UI, create a Case, and see it persisted.

What it deliberately does NOT contain: the agent. There is one clearly marked
spot below ("BUILD YOUR AGENT HERE") where you plug in the autonomous resolution
agent you build. You are free to change anything in this file, restructure the
flow, add endpoints, or replace it entirely — it is a starting point, not a
contract.

Run it:
    python3 server.py            # -> http://localhost:8000
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from advocate.models import Case, ResolutionPolicy
from advocate.store import CaseStore

DB_PATH = os.environ.get("ADVOCATE_DB", "advocate.db")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def run_agent(case_id: str) -> None:
    """
    ============================  BUILD YOUR AGENT HERE  ============================
    This is where your autonomous resolution agent runs for a Case. It is invoked
    on a background thread when a Case is created.

    A working approach (you decide the real design):
      1. Load the Case:           store = CaseStore(DB_PATH); case = store.get(case_id)
      2. Plan a strategy with an LLM (use advocate.llm.LLMClient).
      3. Open a channel and state the case. A simulated counterparty is provided
         in advocate/channels/mock.py so you can develop without a real website;
         later, build a real channel (Playwright / email).
      4. Loop: read the reply -> interpret it -> decide an action under the
         user's ResolutionPolicy (accept / counter / escalate / ask the user) ->
         act. Persist after each step with store.save(case) so the UI updates.
      5. Stop when resolved, denied, or the escalation budget is exhausted.

    The provided pieces you can build on: LLMClient (advocate/llm.py), the Case
    data model (advocate/models.py), the CaseStore (advocate/store.py), and the
    MockChannel sandbox (advocate/channels/mock.py). See the README.

    The agent lives in advocate/agent/. The strategist (planning), classifier
    (perception), deterministic negotiator, and orchestrator loop are wired
    together here.
    --------------------------------------------------------------------------------
    """
    from advocate.agent.orchestrator import resolve_case
    resolve_case(case_id, DB_PATH)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
        if path == "/api/cases":
            store = CaseStore(DB_PATH)
            try:
                return self._json([{"case_id": c.case_id, "status": c.status, "goal": c.goal,
                                    "outcome_amount": c.outcome_amount, "outcome_kind": c.outcome_kind,
                                    "currency": c.policy.currency, "updated_at": c.updated_at}
                                   for c in store.list()])
            finally:
                store.close()
        if path.startswith("/api/cases/"):
            store = CaseStore(DB_PATH)
            try:
                c = store.get(path[len("/api/cases/"):])
                return self._json(c.to_dict() if c else {"error": "not found"}, 200 if c else 404)
            finally:
                store.close()
        return self.send_error(404)

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/cases":
            spec = self._body()
            context = spec.get("context", {})
            context["_counterparty_mode"] = spec.get("counterparty", "llm")
            case = Case(goal=spec.get("goal", ""),
                        policy=ResolutionPolicy.from_dict(spec.get("policy", {})),
                        context=context, evidence=spec.get("evidence", []))
            store = CaseStore(DB_PATH)
            store.save(case)
            store.close()
            threading.Thread(target=run_agent, args=(case.case_id,), daemon=True).start()
            return self._json({"case_id": case.case_id})
        if path.startswith("/api/cases/") and path.endswith("/approve"):
            case_id = path[len("/api/cases/"):-len("/approve")]
            body = self._body()
            store = CaseStore(DB_PATH)
            try:
                case = store.get(case_id)
                if case:
                    # Record the human's reply. Your agent decides what to do with it.
                    approved = bool(body.get("approved", False))
                    note = body.get("note", "")
                    case.add_message(
                        "system",
                        "User reply: %s%s" % (
                            "approved" if approved else "rejected",
                            (" — " + note) if note else "",
                        ),
                        meta={"kind": "user_reply", "approved": approved, "note": note},
                    )
                    store.save(case)
            finally:
                store.close()
            threading.Thread(target=run_agent, args=(case_id,), daemon=True).start()
            return self._json({"ok": True})
        if path.startswith("/api/cases/") and path.endswith("/reply"):
            case_id = path[len("/api/cases/"):-len("/reply")]
            body = self._body()
            text = str(body.get("text", "")).strip()
            if not text:
                return self._json({"error": "reply text required"}, 400)
            store = CaseStore(DB_PATH)
            try:
                case = store.get(case_id)
                if not case:
                    return self._json({"error": "not found"}, 404)
                replies = case.context.get("_manual_replies", [])
                replies.append(text)
                case.context["_manual_replies"] = replies
                case.add_message("system", "Manual support reply queued.")
                store.save(case)
            finally:
                store.close()
            threading.Thread(target=run_agent, args=(case_id,), daemon=True).start()
            return self._json({"ok": True})
        return self.send_error(404)

    def do_DELETE(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/cases":
            store = CaseStore(DB_PATH)
            try:
                cases = store.list()
                for case in cases:
                    store.delete(case.case_id)
                return self._json({"ok": True, "deleted": len(cases)})
            finally:
                store.close()
        if path.startswith("/api/cases/"):
            case_id = path[len("/api/cases/"):]
            store = CaseStore(DB_PATH)
            try:
                store.delete(case_id)
                return self._json({"ok": True, "deleted": 1})
            finally:
                store.close()
        return self.send_error(404)


def main():
    load_dotenv()
    port = int(os.environ.get("PORT", "8000"))
    print("ResolveMate dashboard at http://localhost:%d  (Ctrl+C to stop)" % port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
