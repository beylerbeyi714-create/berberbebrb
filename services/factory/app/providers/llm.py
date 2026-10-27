"""Metin üretim sağlayıcıları: Anthropic (varsayılan), OpenAI, Gemini."""
from __future__ import annotations

import json
import os

import anthropic

from .. import config
from .base import LLMProvider, LLMResult, ProviderError, ProviderNotConfigured, http, raise_for, register


def _cost(prices: dict, inp: int, out: int) -> float:
    return inp / 1e6 * prices["input_per_mtok"] + out / 1e6 * prices["output_per_mtok"]


@register("llm")
class AnthropicLLM(LLMProvider):
    name = "anthropic"
    estimated_cost = 0.08

    def generate_json(self, system, prompt, schema):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderNotConfigured("ANTHROPIC_API_KEY tanımlı değil")
        client = anthropic.Anthropic(max_retries=2)
        try:
            # Opus 5.5 güvenlik sınıflandırıcısı reddederse sunucu taraflı yedek modele düşer.
            resp = client.beta.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": config.ANTHROPIC_EFFORT,
                               "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as e:
            raise ProviderError(f"anthropic rate limit: {e}", retryable=True) from e
        except anthropic.BadRequestError as e:
            raise ProviderError(f"anthropic bad request: {e}", retryable=False) from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"anthropic HTTP {e.status_code}: {e}", retryable=e.status_code >= 500) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"anthropic connection: {e}", retryable=True) from e
        if resp.stop_reason == "refusal":
            raise ProviderError("anthropic: istek reddedildi (refusal)", retryable=False)
        if resp.stop_reason == "max_tokens":
            raise ProviderError("anthropic: max_tokens sınırına ulaşıldı", retryable=True)
        text = next(b.text for b in resp.content if b.type == "text")
        u = resp.usage
        return LLMResult(json.loads(text), u.input_tokens, u.output_tokens,
                         _cost(config.PRICING["anthropic"], u.input_tokens, u.output_tokens), resp.model)


@register("llm")
class OpenAILLM(LLMProvider):
    name = "openai"
    estimated_cost = 0.02

    def generate_json(self, system, prompt, schema):
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderNotConfigured("OPENAI_API_KEY tanımlı değil")
        with http() as c:
            r = c.post("https://api.openai.com/v1/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": config.OPENAI_TEXT_MODEL,
                             "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": prompt}],
                             "response_format": {"type": "json_schema",
                                                 "json_schema": {"name": "result", "schema": schema,
                                                                 "strict": True}}})
        raise_for(r, "openai")
        d = r.json()
        u = d.get("usage", {})
        inp, out = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        return LLMResult(json.loads(d["choices"][0]["message"]["content"]), inp, out,
                         _cost(config.PRICING["openai_text"], inp, out), d.get("model", ""))


@register("llm")
class GeminiLLM(LLMProvider):
    name = "gemini"
    estimated_cost = 0.02

    def generate_json(self, system, prompt, schema):
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ProviderNotConfigured("GEMINI_API_KEY tanımlı değil")
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{config.GEMINI_TEXT_MODEL}:generateContent")
        with http() as c:
            r = c.post(url, headers={"x-goog-api-key": key},
                       json={"systemInstruction": {"parts": [{"text": system}]},
                             "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                             "generationConfig": {"responseMimeType": "application/json",
                                                  "responseJsonSchema": schema}})
        raise_for(r, "gemini")
        d = r.json()
        text = d["candidates"][0]["content"]["parts"][0]["text"]
        u = d.get("usageMetadata", {})
        inp, out = u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)
        return LLMResult(json.loads(text), inp, out, _cost(config.PRICING["gemini_text"], inp, out),
                         config.GEMINI_TEXT_MODEL)
