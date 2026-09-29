import os
import json
import httpx
from pydantic import BaseModel
from typing import Type, TypeVar, Any, Optional
from google import genai
from google.genai import types

from src.loader import load_env

load_env()

T = TypeVar("T", bound=BaseModel)

def classify_llm_error(e: Exception) -> str:
    err_str = str(e).lower()
    if isinstance(e, httpx.TimeoutException) or "timeout" in err_str or "deadline" in err_str or "timed out" in err_str:
        return "llm_timeout"
    if "429" in err_str or "resource_exhausted" in err_str or "rate limit" in err_str or "quota" in err_str:
        return "llm_rate_limited"
    if isinstance(e, (json.JSONDecodeError, ValueError)) and any(k in err_str for k in ["json", "validation", "schema", "expecting value"]):
        return "llm_invalid_response"
    if isinstance(e, (httpx.ConnectError, httpx.NetworkError)) or any(k in err_str for k in ["500", "502", "503", "unavailable", "connection", "connect"]):
        return "llm_service_error"
    return "llm_service_error"

class LLMClient:
    """
    Multi-provider LLM client with Grok as primary and Gemini as fallback.
    If both fail, time out, are rate-limited, or unauthorized, the calling
    layer falls back to deterministic grounded execution.
    """
    def __init__(self):
        load_env()
        self.timeout_seconds = float(os.environ.get("LLM_TIMEOUT", 15.0))
        self.last_failure_reason: Optional[str] = None

        self.grok_key = (
            os.environ.get("GROK") or
            os.environ.get("GROK_API_KEY") or
            os.environ.get("GROQ_API_KEY") or
            os.environ.get("XAI_API_KEY")
        )
        # Determine Grok/Groq endpoint
        if self.grok_key and self.grok_key.startswith("gsk_"):
            self.grok_url = "https://api.groq.com/openai/v1/chat/completions"
            self.grok_model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        else:
            self.grok_url = "https://api.x.ai/v1/chat/completions"
            self.grok_model = os.environ.get("GROK_MODEL", "grok-beta")

        # Gemini Fallback Client
        self.gemini_key = os.environ.get("GEMINI_API_KEY")
        self.gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.gemini_client = None
        if self.gemini_key:
            try:
                self.gemini_client = genai.Client(
                    api_key=self.gemini_key,
                    http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000))
                )
            except Exception:
                self.gemini_client = None

    def generate_structured(self, prompt: str, schema: Type[T]) -> T:
        self.last_failure_reason = None

        # 1. Attempt Grok (Primary)
        if self.grok_key:
            try:
                content = self._call_grok(prompt, expect_json=True)
                if content:
                    clean_content = content.strip()
                    if clean_content.startswith("```json"):
                        clean_content = clean_content[7:]
                    if clean_content.startswith("```"):
                        clean_content = clean_content[3:]
                    if clean_content.endswith("```"):
                        clean_content = clean_content[:-3]
                    clean_content = clean_content.strip()
                    
                    if hasattr(schema, "model_validate_json"):
                        return schema.model_validate_json(clean_content)
                    return schema.parse_raw(clean_content)
            except Exception as e:
                self.last_failure_reason = classify_llm_error(e)
                print(f"[LLM Notice] Grok primary provider unavailable ({self.last_failure_reason}: {e}). Falling back to Gemini...")

        # 2. Attempt Gemini (Secondary Fallback)
        if self.gemini_client:
            try:
                response = self.gemini_client.models.generate_content(
                    model=self.gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=0.0,
                        http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000))
                    )
                )
                if hasattr(schema, "model_validate_json"):
                    return schema.model_validate_json(response.text)
                return schema.parse_raw(response.text)
            except Exception as e:
                self.last_failure_reason = classify_llm_error(e)
                print(f"[LLM Notice] Gemini fallback provider unavailable ({self.last_failure_reason}: {e}).")

        fail_reason = self.last_failure_reason or "llm_service_error"
        raise RuntimeError(f"{fail_reason}: All LLM providers (Grok primary and Gemini fallback) unavailable or unauthorized.")

    def generate_text(self, prompt: str) -> str:
        # 1. Attempt Grok (Primary)
        if self.grok_key:
            try:
                content = self._call_grok(prompt, expect_json=False)
                if content:
                    return content
            except Exception as e:
                print(f"[LLM Notice] Grok primary provider unavailable ({e}). Falling back to Gemini...")

        # 2. Attempt Gemini (Secondary Fallback)
        if self.gemini_client:
            try:
                response = self.gemini_client.models.generate_content(
                    model=self.gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.0)
                )
                return response.text
            except Exception as e:
                print(f"[LLM Notice] Gemini fallback provider unavailable ({e}).")

        raise RuntimeError("All LLM providers unavailable.")

    def _call_grok(self, prompt: str, expect_json: bool = False) -> str:
        headers = {
            "Authorization": f"Bearer {self.grok_key}",
            "Content-Type": "application/json"
        }
        system_msg = "You are a grounded travel assistant. Output ONLY valid JSON matching the requested structure." if expect_json else "You are a grounded travel assistant."
        payload = {
            "model": self.grok_model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0
        }
        if expect_json:
            payload["response_format"] = {"type": "json_object"}

        res = httpx.post(self.grok_url, headers=headers, json=payload, timeout=self.timeout_seconds)
        if res.status_code == 429:
            raise ValueError(f"HTTP 429 rate limit exceeded: {res.text}")
        if res.status_code != 200:
            raise ValueError(f"HTTP {res.status_code}: {res.text}")

        data = res.json()
        return data["choices"][0]["message"]["content"]
