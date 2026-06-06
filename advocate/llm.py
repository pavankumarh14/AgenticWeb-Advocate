"""
Provider-agnostic LLM client.
================================

This is the ONLY file you need to touch to switch LLM providers
(OpenAI / Azure OpenAI / Google Gemini / Anthropic Claude / Groq / DeepSeek /
a local model / etc.).

The rest of the codebase talks to an LLM exclusively through `LLMClient.chat()`
and `LLMClient.chat_json()` below, so as long as those two methods return what
they promise, everything else keeps working unchanged.

Design goals
------------
* Zero third-party dependencies — uses only the Python standard library
  (`urllib`). You do NOT need to `pip install openai`.
* One small surface area: a chat-completions style call that takes a list of
  ``{"role": ..., "content": ...}`` messages and returns a string.

Configuration (environment variables)
--------------------------------------
* ADVOCATE_LLM_PROVIDER   default: "openai"
* ADVOCATE_LLM_API_KEY    your API key (falls back to OPENAI_API_KEY)
* ADVOCATE_LLM_MODEL      default: "gpt-4o-mini"
* ADVOCATE_LLM_BASE_URL   default: provider default
* ADVOCATE_AZURE_OPENAI_API_VERSION default: "2024-02-15-preview"

>>> SWITCHING PROVIDERS — read PROVIDERS below and README section
    "Using a different LLM". Most OpenAI-compatible providers (Groq, DeepSeek,
    Together, local llama.cpp/Ollama servers) work by ONLY changing the env
    vars — no code change needed. Anthropic Claude needs the small adapter
    that is already stubbed out in `_chat_anthropic()`.
"""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Provider presets. Add or tweak entries here when onboarding a new provider.
# Anything that speaks the OpenAI /chat/completions format only needs an entry
# with the right base_url; no new code path is required.
# ---------------------------------------------------------------------------
PROVIDERS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "format": "openai",
    },
    "azure_openai": {
        "base_url": "",
        "default_model": "gpt-4o-mini",
        "format": "azure_openai",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.5-flash",
        "format": "gemini",
    },
    # --- OpenAI-compatible providers: just set the API key + model via env ---
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "format": "openai",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "format": "openai",
    },
    "ollama": {  # local, free, no key needed
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.1",
        "format": "openai",
    },
    # --- Anthropic uses a slightly different request/response shape ----------
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-6",
        "format": "anthropic",
    },
}


class LLMError(RuntimeError):
    """Raised when the LLM call fails after retries."""


class LLMClient:
    """A tiny, dependency-free chat client.

    Usage::

        llm = LLMClient()
        text = llm.chat([{"role": "user", "content": "Hello"}])
        data = llm.chat_json([{"role": "user", "content": "Return JSON ..."}])
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 60,
    ):
        self.provider = (provider or os.getenv("ADVOCATE_LLM_PROVIDER", "openai")).lower()
        if self.provider not in PROVIDERS:
            raise LLMError(
                "Unknown provider %r. Known: %s. Add one in advocate/llm.py:PROVIDERS."
                % (self.provider, ", ".join(PROVIDERS))
            )
        preset = PROVIDERS[self.provider]
        self.format = preset["format"]
        self.base_url = (base_url or os.getenv("ADVOCATE_LLM_BASE_URL") or preset["base_url"]).rstrip("/")
        self.model = model or os.getenv("ADVOCATE_LLM_MODEL") or preset["default_model"]
        self.azure_api_version = os.getenv("ADVOCATE_AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
        self.api_key = (
            api_key
            or os.getenv("ADVOCATE_LLM_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("AZURE_OPENAI_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ("ollama" if self.provider == "ollama" else None)
        )
        self.timeout = timeout
        if not self.api_key:
            raise LLMError(
                "No API key found. Set ADVOCATE_LLM_API_KEY (or OPENAI_API_KEY) "
                "in your environment or .env file."
            )
        if self.format == "azure_openai" and not self.base_url:
            raise LLMError(
                "Azure OpenAI needs ADVOCATE_LLM_BASE_URL, e.g. "
                "https://YOUR-RESOURCE.openai.azure.com"
            )

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 800,
        json_mode: bool = False,
    ) -> str:
        """Send chat messages, return the assistant's reply as a string."""
        if self.format == "anthropic":
            return self._chat_anthropic(messages, temperature, max_tokens)
        if self.format == "azure_openai":
            return self._chat_azure_openai(messages, temperature, max_tokens, json_mode)
        if self.format == "gemini":
            return self._chat_gemini(messages, temperature, max_tokens, json_mode)
        return self._chat_openai(messages, temperature, max_tokens, json_mode)

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> dict:
        """Like ``chat`` but parses (and best-effort repairs) a JSON object."""
        raw = self.chat(messages, temperature, max_tokens, json_mode=True)
        return _parse_json_object(raw)

    # -- provider implementations ------------------------------------------

    def _chat_openai(self, messages, temperature, max_tokens, json_mode) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            # Supported by OpenAI gpt-4o family. Harmless hint elsewhere; if a
            # provider rejects it, drop this and rely on prompt instructions.
            payload["response_format"] = {"type": "json_object"}
        data = self._post(
            self.base_url + "/chat/completions",
            payload,
            {"Authorization": "Bearer " + self.api_key},
        )
        return data["choices"][0]["message"]["content"]

    def _chat_azure_openai(self, messages, temperature, max_tokens, json_mode) -> str:
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        url = (
            self.base_url
            + "/openai/deployments/"
            + self.model
            + "/chat/completions?api-version="
            + self.azure_api_version
        )
        data = self._post(url, payload, {"api-key": self.api_key})
        return data["choices"][0]["message"]["content"]

    def _chat_gemini(self, messages, temperature, max_tokens, json_mode) -> str:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [m for m in messages if m["role"] != "system"]
        contents = []
        if system:
            contents.append({
                "role": "user",
                "parts": [{"text": "System instructions:\n" + system}],
            })
        for message in convo:
            role = "model" if message["role"] == "assistant" else "user"
            contents.append({
                "role": role,
                "parts": [{"text": message["content"]}],
            })
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        url = self.base_url + "/models/" + self.model + ":generateContent"
        data = self._post(url, payload, {"x-goog-api-key": self.api_key})
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMError("Unexpected Gemini response: %s" % data) from exc

    def _chat_anthropic(self, messages, temperature, max_tokens) -> str:
        # Anthropic separates the system prompt from the message list.
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [m for m in messages if m["role"] != "system"]
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": convo,
        }
        if system:
            payload["system"] = system
        data = self._post(
            self.base_url + "/messages",
            payload,
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        return data["content"][0]["text"]

    # -- transport ----------------------------------------------------------

    def _post(self, url: str, payload: dict, extra_headers: Dict[str, str]) -> dict:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        headers.update(extra_headers)
        last_err = None
        for attempt in range(3):
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "ignore")
                last_err = LLMError("HTTP %s from %s: %s" % (e.code, url, detail))
                # 429 / 5xx are worth retrying; 4xx config errors are not.
                if e.code not in (429, 500, 502, 503, 504):
                    raise last_err
            except urllib.error.URLError as e:
                last_err = LLMError("Network error calling %s: %s" % (url, e.reason))
            time.sleep(1.5 * (attempt + 1))
        raise last_err  # type: ignore[misc]


def _parse_json_object(raw: str) -> dict:
    """Best-effort extraction of a JSON object from an LLM reply."""
    raw = raw.strip()
    # Strip ```json ... ``` fences if present.
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(raw[start:end + 1])
        raise LLMError("Could not parse JSON from LLM reply:\n%s" % raw)
