"""
Mocked AI provider coverage (section 2 of the hardening spec: "mocks/fakes for
AI provider"). httpx.post is monkeypatched so no real network call is ever
made to OpenAI or a local Ollama server.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.ai.factory import get_ai_provider
from app.ai.ollama_provider import OllamaProvider
from app.ai.openai_provider import OpenAIProvider
from app.config import Settings


class _FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json_body = json_body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_body


# --- OpenAI ---

def test_openai_classify_job_email_parses_response(monkeypatch):
    def fake_post(url, headers, json, timeout):
        content = json_module_dumps({"is_job_email": True, "confidence": 0.92, "reason": "clear JD"})
        return _FakeResponse({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")
    result = provider.classify_job_email(subject="Senior Data Engineer", body="Role: ...")
    assert result["is_job_email"] is True
    assert result["confidence"] == 0.92


def test_openai_extract_job_details_parses_response(monkeypatch):
    def fake_post(url, headers, json, timeout):
        content = json_module_dumps({
            "job_title": "Senior Data Engineer", "job_location": "Irvine, CA",
            "requirements": ["python", "sql"],
        })
        return _FakeResponse({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAIProvider(api_key="sk-test")
    result = provider.extract_job_details("Role: Senior Data Engineer\nLocation: Irvine, CA")
    assert result["job_title"] == "Senior Data Engineer"
    assert result["requirements"] == ["python", "sql"]


def test_openai_polish_email_body_returns_text(monkeypatch):
    def fake_post(url, headers, json, timeout):
        return _FakeResponse({"choices": [{"message": {"content": "Hi Naveen,\nPolished body.\n"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAIProvider(api_key="sk-test")
    polished = provider.polish_email_body("Hi Naveen,\nOriginal body.\n", constraints=["never mention location"])
    assert "Polished body" in polished


def test_openai_provider_requires_api_key():
    with pytest.raises(ValueError):
        OpenAIProvider(api_key="")


def test_openai_http_error_propagates_to_caller(monkeypatch):
    def fake_post(url, headers, json, timeout):
        return _FakeResponse({}, status_code=500)

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAIProvider(api_key="sk-test")
    with pytest.raises(Exception):
        provider.classify_job_email("subject", "body")


# --- Ollama ---

def test_ollama_classify_job_email_parses_response(monkeypatch):
    def fake_post(url, json, timeout):
        assert "/api/generate" in url
        body = json_module_dumps({"is_job_email": True, "confidence": 0.8, "reason": "matches JD pattern"})
        return _FakeResponse({"response": body})

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OllamaProvider(base_url="http://localhost:11434", model="llama3.1")
    result = provider.classify_job_email(subject="Data Engineer", body="Role: ...")
    assert result["is_job_email"] is True


def test_ollama_extract_job_details_parses_response(monkeypatch):
    def fake_post(url, json, timeout):
        body = json_module_dumps({"job_title": "Data Engineer", "job_location": None, "requirements": []})
        return _FakeResponse({"response": body})

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OllamaProvider()
    result = provider.extract_job_details("Role: Data Engineer")
    assert result["job_title"] == "Data Engineer"
    assert result["job_location"] is None


def test_ollama_polish_email_body_returns_text(monkeypatch):
    def fake_post(url, json, timeout):
        return _FakeResponse({"response": "Polished via Ollama."})

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OllamaProvider()
    result = provider.polish_email_body("original", constraints=[])
    assert result == "Polished via Ollama."


# --- Factory ---

def test_factory_returns_none_for_ai_provider_none():
    settings = Settings(AI_PROVIDER="none")
    assert get_ai_provider(settings) is None


def test_factory_returns_openai_provider_when_configured():
    settings = Settings(AI_PROVIDER="openai", OPENAI_API_KEY="sk-test")
    provider = get_ai_provider(settings)
    assert isinstance(provider, OpenAIProvider)


def test_factory_returns_ollama_provider_when_configured():
    settings = Settings(AI_PROVIDER="ollama", OLLAMA_BASE_URL="http://localhost:11434")
    provider = get_ai_provider(settings)
    assert isinstance(provider, OllamaProvider)


def json_module_dumps(obj):
    return json.dumps(obj)
