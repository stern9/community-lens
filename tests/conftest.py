import io
import ipaddress
import json
import os
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from community_lens import config, llm

FIX = Path(__file__).parent / "fixtures"
config.LOAD_DOTENV = False  # las pruebas nunca leen el .env de quien las corre


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Cada prueba usa su propia base y arranca con la configuración por defecto: sin .env y sin
    ninguna variable de la app que la terminal tenga exportada (credenciales incluidas)."""
    for var in config.ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CL_DB_PATH", str(tmp_path / "test.db"))
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


@pytest.fixture
def setenv(monkeypatch):
    """Cambia variables de configuración a mitad de una prueba (la app las lee una sola vez, al arrancar)."""
    def _set(**values: str) -> None:
        for k, v in values.items():
            monkeypatch.setenv(k, v)
        config.settings.cache_clear()
    return _set


@pytest.fixture
def con():
    from community_lens.db import connect
    c = connect()
    yield c
    c.close()


def fixture_bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()


def do_import(con, name, chat_id=None, date_order=None, tz="America/Costa_Rica", file_name=None, data=None, **kw):
    from community_lens import importer
    data = data if data is not None else fixture_bytes(name)
    pv = importer.preview(con, data, file_name or name, tz, date_order)
    assert not pv["needs_date_order"], pv["date_detection"]
    return importer.commit(con, pv["token"], file_name or name, chat_id=chat_id, chat_name=None,
                           tz_name=tz, date_order=pv["date_order"], **kw)


class FakeNet:
    """Red falsa para newspaper.http_get: reemplaza solo la resolución de nombres y el GET.
    `routes(url)` devuelve (estado, encabezados, cuerpo); `requests` anota cada solicitud que se llegó a emitir."""

    def __init__(self, routes, dns=None):
        self.routes, self.dns, self.requests = routes, {"localhost": ["127.0.0.1"], **(dns or {})}, []

    def resolve(self, host, port):
        if host in self.dns:
            return self.dns[host]
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            return ["93.184.216.34"]  # cualquier otro nombre: una dirección pública de mentira

    def get(self, parts, ip):
        self.requests.append((parts.geturl(), ip))
        status, headers, body = self.routes(parts.geturl())
        msg = Message()
        for k, v in headers.items():
            msg[k] = v
        return status, msg, body.encode() if isinstance(body, str) else body


class FakeLLM(llm.Transport):
    """Proveedores de modelos falsos: reemplaza solo el envío (HTTP), no el armado de la solicitud ni la lectura
    de la respuesta. `answer(proveedor, system, user)` devuelve el texto que "escribe" el modelo, o un número para
    responder con ese código HTTP. `requests` anota (proveedor, url, encabezados, cuerpo) de cada solicitud."""

    def __init__(self, answer):
        self.answer, self.requests = answer, []

    def open(self, req):  # OpenAI (y compatibles) y Gemini
        body = json.loads(req.data)
        if "generativelanguage" in req.full_url:
            name, system, user = "gemini", body["systemInstruction"]["parts"][0]["text"], body["contents"][0]["parts"][0]["text"]
            wrap = lambda text: {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        else:
            name, system, user = "openai", body["messages"][0]["content"], body["messages"][1]["content"]
            wrap = lambda text: {"choices": [{"message": {"role": "assistant", "content": text}}]}
        self.requests.append((name, req.full_url, {k.lower(): v for k, v in req.header_items()}, body))
        out = self.answer(name, system, user)
        if isinstance(out, int):
            raise urllib.error.HTTPError(req.full_url, out, "error", Message(), io.BytesIO(b'{"error": "el cuerpo no debe mostrarse"}'))
        return io.BytesIO(json.dumps(wrap(out)).encode())

    def sdk_options(self):  # Anthropic: el SDK real, con un cliente HTTP que no sale a la red
        import httpx2

        def handler(request):
            body = json.loads(request.content)
            self.requests.append(("anthropic", str(request.url), dict(request.headers), body))
            out = self.answer("anthropic", body["system"], body["messages"][0]["content"])
            if isinstance(out, int):
                return httpx2.Response(out, json={"type": "error", "error": {"type": "api_error", "message": "el cuerpo no debe mostrarse"}})
            return httpx2.Response(200, json={
                "id": "msg_prueba", "type": "message", "role": "assistant", "model": body["model"], "stop_sequence": None,
                "content": [{"type": "text", "text": out}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}})
        return {"http_client": httpx2.Client(transport=httpx2.MockTransport(handler)), "max_retries": 0}
