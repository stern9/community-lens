"""Lo que viaja a cada proveedor de modelos y cómo se traduce cada respuesta o error.
Sin red: solo se reemplaza el envío (llm.Transport); la solicitud la arma el código real (y, para Anthropic, su SDK)."""
import io
import json
import urllib.error

import pytest

from community_lens import llm
from conftest import FakeLLM

KEY = "sk-clave-secreta-123"
PROVIDERS = {
    "anthropic": llm.Provider("anthropic", "Claude", "claude-demo", KEY),
    "openai": llm.Provider("openai", "OpenAI", "gpt-demo", KEY, "https://api.openai.example/v1"),
    "gemini": llm.Provider("gemini", "Gemini", "gemini-demo", KEY),
}


def ask(name, answer):
    t = FakeLLM(answer if callable(answer) else lambda *a: answer)
    return llm.complete_json(PROVIDERS[name], "Sos el editor.", "Texto de las páginas.", t), t


def test_anthropic_request():
    data, t = ask("anthropic", '{"hilo": "x"}')
    (name, url, headers, body), = t.requests
    assert data == {"hilo": "x"} and url.startswith("https://api.anthropic.com/v1/messages")
    assert headers["x-api-key"] == KEY and "server-side-fallback-2026-07-01" in headers["anthropic-beta"]
    assert body == {"model": "claude-demo", "max_tokens": llm.MAX_OUTPUT_TOKENS, "fallbacks": "default", "system": "Sos el editor.",
                    "messages": [{"role": "user", "content": "Texto de las páginas."}]}


def test_openai_request_uses_the_configured_base_url():
    data, t = ask("openai", '{"hilo": "x"}')
    (name, url, headers, body), = t.requests
    assert data == {"hilo": "x"} and url == "https://api.openai.example/v1/chat/completions"
    assert headers["authorization"] == f"Bearer {KEY}" and headers["content-type"] == "application/json"
    assert body == {"model": "gpt-demo", "messages": [{"role": "system", "content": "Sos el editor."},
                                                      {"role": "user", "content": "Texto de las páginas."}]}


def test_gemini_request_keeps_the_key_out_of_the_url():
    data, t = ask("gemini", '{"hilo": "x"}')
    (name, url, headers, body), = t.requests
    assert data == {"hilo": "x"} and url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-demo:generateContent"
    assert headers["x-goog-api-key"] == KEY and KEY not in url
    assert body == {"systemInstruction": {"parts": [{"text": "Sos el editor."}]},
                    "contents": [{"role": "user", "parts": [{"text": "Texto de las páginas."}]}]}


@pytest.mark.parametrize("name", list(PROVIDERS))
def test_answer_must_be_a_json_object(name):
    assert ask(name, '```json\n{"a": [1, 2]}\n```')[0] == {"a": [1, 2]}       # se tolera el bloque de código
    assert ask(name, '  {"a": 1}\n')[0] == {"a": 1}
    for text, why in (("Claro, acá va: {}", "JSON válido"), ("", "JSON válido"), ("[1, 2]", "un objeto JSON"), ('"hola"', "un objeto JSON")):
        with pytest.raises(llm.LLMError, match=why) as e:
            ask(name, text)
        assert PROVIDERS[name].label in str(e.value)


@pytest.mark.parametrize("name, status, message", [
    ("anthropic", 401, "La key de Anthropic no es válida."), ("anthropic", 429, "límite de uso"),
    ("anthropic", 529, "Anthropic respondió HTTP 529."), ("anthropic", 400, "Anthropic respondió HTTP 400."),
    ("openai", 401, "El proveedor respondió HTTP 401."), ("openai", 429, "El proveedor respondió HTTP 429."),
    ("openai", 500, "El proveedor respondió HTTP 500."), ("gemini", 403, "El proveedor respondió HTTP 403."),
    ("gemini", 503, "El proveedor respondió HTTP 503."),
])
def test_http_errors_become_short_messages_without_the_key(name, status, message):
    with pytest.raises(llm.LLMError) as e:
        ask(name, status)
    assert message in str(e.value) and KEY not in str(e.value) and "no debe mostrarse" not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__      # ni en la cadena de la excepción


class Broken(llm.Transport):
    """Transporte que falla antes de obtener respuesta, o devuelve un cuerpo fijo."""

    def __init__(self, error=None, body=b""):
        self.error, self.body = error, body

    def open(self, req):
        if self.error:
            raise self.error
        return io.BytesIO(self.body)

    def sdk_options(self):
        import httpx2

        def handler(request):
            if self.error:
                raise httpx2.ConnectError(f"sin conexión ({KEY})")
            return httpx2.Response(200, content=self.body, headers={"content-type": "application/json"})
        return {"http_client": httpx2.Client(transport=httpx2.MockTransport(handler)), "max_retries": 0}


@pytest.mark.parametrize("name", list(PROVIDERS))
@pytest.mark.parametrize("error", [urllib.error.URLError(f"sin red ({KEY})"), TimeoutError(), ConnectionResetError()])
def test_connection_failures(name, error):
    with pytest.raises(llm.LLMError, match="No se pudo conectar") as e:
        llm.complete(PROVIDERS[name], "s", "u", Broken(error))
    assert KEY not in str(e.value)


@pytest.mark.parametrize("name, body", [
    ("openai", b"<html>502 Bad Gateway</html>"), ("openai", b"\xff\xfe"), ("openai", b"[]"), ("openai", b'{"choices": []}'),
    ("openai", b'{"choices": [{"message": {}}]}'), ("openai", b'{"choices": [{"message": {"content": [{"type": "text"}]}}]}'),
    ("openai", b'{"error": {"message": "x"}}'),
    ("gemini", b"no es json"), ("gemini", b'{"candidates": []}'), ("gemini", b'{"candidates": [{"finishReason": "SAFETY"}]}'),
    ("gemini", b'{"candidates": [{"content": {"parts": ["texto suelto"]}}]}'), ("gemini", b'{"candidates": [{"content": {"parts": [{"text": 5}]}}]}'),
    ("gemini", b'{"promptFeedback": {"blockReason": "OTHER"}}'),
])
def test_malformed_responses_are_errors_not_crashes(name, body):
    with pytest.raises(llm.LLMError, match="forma esperada|respuesta no fue válida"):
        llm.complete(PROVIDERS[name], "s", "u", Broken(body=body))


def test_openai_empty_content_and_gemini_multiple_parts():
    assert llm.complete(PROVIDERS["openai"], "s", "u", Broken(body=b'{"choices": [{"message": {"content": null}}]}')) == ""
    parts = json.dumps({"candidates": [{"content": {"parts": [{"text": '{"a":'}, {"text": " 1}"}, {"thought": True}]}}]}).encode()
    assert llm.complete_json(PROVIDERS["gemini"], "s", "u", Broken(body=parts)) == {"a": 1}


@pytest.mark.parametrize("stop, message", [("refusal", "declinó"), ("max_tokens", "truncada")])
def test_anthropic_stop_reasons(stop, message):
    body = json.dumps({"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-demo", "stop_sequence": None,
                       "content": [{"type": "text", "text": '{"a": 1'}], "stop_reason": stop, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
    with pytest.raises(llm.LLMError, match=message):
        llm.complete(PROVIDERS["anthropic"], "s", "u", Broken(body=body))


def test_provider_list_and_public_shape_never_carry_the_key(setenv):
    setenv(ANTHROPIC_API_KEY=KEY, ANTHROPIC_LABEL="Opus", OPENAI_API_KEY=KEY, GEMINI_API_KEY=KEY, GEMINI_MODEL=" gemini-demo ",
           OPENAI_BASE_URL="https://api.openai.example/v1/")
    ps = llm.providers()
    assert [(p.name, p.label, p.model) for p in ps] == [("anthropic", "Opus", "claude-opus-5-5"), ("gemini", "Gemini", "gemini-demo")]
    assert llm.missing_model() == ["OpenAI"] and KEY not in json.dumps([p.public() for p in ps])
    with pytest.raises(llm.LLMError, match="no está configurado"):
        llm.get("openai")
    setenv(OPENAI_MODEL="gpt-demo")
    assert llm.get("openai").base_url == "https://api.openai.example/v1" and llm.missing_model() == []
