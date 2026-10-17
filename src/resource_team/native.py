"""Actual Google SDK/Gemini wrapper over per-run owned public sync/async HTTPX."""

import asyncio
import base64
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .completion import MAX_RESPONSE_BYTES, TOOLS, completed, usage_of
from .evidence import error_info
from .strict import canonical, digest, parse_json, sha256, text


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str
    base_url: str

    def target(self):
        return {
            "provider": "gemini",
            "model": self.model,
            "base_url": self.base_url,
            "credential_sha256": sha256(self.api_key.encode()),
        }


def config(api_key=None, model=None, base_url=None):
    key = os.environ.get("GOOGLE_API_KEY") if api_key is None else api_key
    selected = os.environ.get("GEMINI_MODEL") if model is None else model
    if (
        type(key) is not str
        or not 1 <= len(key) <= 1024
        or any(ord(c) < 33 or ord(c) > 126 for c in key)
    ):
        raise ValueError("explicit GOOGLE_API_KEY required")
    if type(selected) is not str or not re.fullmatch(
        r"gemini-[a-z0-9._-]{1,127}", selected
    ):
        raise ValueError("explicit enabled GEMINI_MODEL identifier required")
    base = "https://generativelanguage.googleapis.com" if base_url is None else base_url
    if type(base) is not str:
        raise ValueError("explicit Gemini origin required")
    p = urlsplit(base)
    if (
        p.username
        or p.password
        or p.query
        or p.fragment
        or p.path not in ("", "/")
        or p.hostname is None
        or p.scheme not in ("https", "http")
        or p.scheme == "http"
        and (p.hostname != "127.0.0.1" or p.port is None)
    ):
        raise ValueError("HTTPS or explicit owned loopback Gemini origin required")
    for name, value in os.environ.items():
        if value and (
            name.startswith("OTEL_EXPORTER_")
            or name
            in (
                "GOOGLE_GENAI_CUSTOM_HEADERS",
                "GOOGLE_CUSTOM_HEADERS",
                "ADK_TELEMETRY_IGNORE_RUN_CONFIG",
            )
        ):
            raise ValueError(
                "unsupported ambient tracing or custom header configuration: " + name
            )
    from opentelemetry import trace

    if not isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        raise ValueError("ambient configured global tracing provider is unsupported")
    return Config(key, selected, base.rstrip("/"))


class Transport(httpx.AsyncBaseTransport):
    def __init__(self, native):
        self.native, self.owner = native, native.owner
        self.close_attempted = self.closed = False
        self.inner = httpx.AsyncHTTPTransport(
            trust_env=False,
            retries=0,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )

    async def handle_async_request(self, request):
        self.owner.check()
        pending = self.native.pending
        if (
            pending is None
            or pending["sent"]
            or len(self.owner.receipt.data["calls"]) >= 24
        ):
            raise ValueError(
                "one prepared Gemini request per bounded actual ADK turn required"
            )
        raw = await request.aread()
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("original Gemini request exceeds2MiB")
        body = parse_json(raw)
        cfg = self.native.config
        if (
            request.method != "POST"
            or str(request.url)
            != cfg.base_url + "/v1beta/models/" + cfg.model + ":generateContent"
            or request.headers.get("x-goog-api-key") != cfg.api_key
        ):
            raise ValueError("exact owned Gemini model/endpoint/explicit key required")
        if request.headers.get("x-server-timeout") != "300":
            raise ValueError("exact configured Gemini server timeout required")
        allowed = {
            "host",
            "accept",
            "accept-encoding",
            "connection",
            "user-agent",
            "content-type",
            "content-length",
            "x-goog-api-key",
            "x-goog-api-client",
            "x-server-timeout",
        }
        for name, value in request.headers.items():
            if name not in allowed:
                raise ValueError("unsupported ambient Gemini header: " + name)
            text(value, 0, 2048)
        if digest(body) != digest(pending["expected"]):
            self.owner.receipt.data.setdefault("rejected_requests", []).append(
                {
                    "request": body,
                    "expected": deepcopy(pending["expected"]),
                    "sent": False,
                }
            )
            self.owner.save()
            raise ValueError("exact frozen ADK role/input/tools/settings required")
        pending["sent"] = True
        call = {
            "ordinal": len(self.owner.receipt.data["calls"]) + 1,
            "role": pending["role"],
            "url": str(request.url),
            "key_present": True,
            "request_base64": base64.b64encode(raw).decode(),
            "request_sha256": sha256(raw),
            "request": body,
            "response": None,
            "response_complete": False,
            "error": None,
        }
        self.owner.receipt.data["calls"].append(call)
        self.owner.save()
        response, phase, chunks, size = None, None, [], 0
        try:
            phase = asyncio.get_running_loop().call_later(
                30,
                lambda: self.owner.cancel(
                    TimeoutError("allocation Gemini phase exceeded30seconds")
                ),
            )
            self.owner.check()
            response = await self.inner.handle_async_request(request)
            call["status_code"] = response.status_code
            call["response_headers"] = [list(p) for p in response.headers.multi_items()]
            async for chunk in response.aiter_bytes():
                take = min(len(chunk), MAX_RESPONSE_BYTES - size)
                chunks.append(chunk[:take])
                size += take
                if take != len(chunk):
                    raise ValueError("original Gemini response exceeds1MiB")
            call["response_complete"] = True
            original_raw = b"".join(chunks)
            original = parse_json(original_raw)
            call["response"] = original
            call["reported_usage"] = usage_of(original)
            if response.status_code != 200:
                raise ValueError("original Gemini HTTP status is not200")
            _, completion = completed(original_raw, pending["role"])
            if completion["response_id"] in self.native.response_ids:
                raise ValueError("original Gemini response identity repeated")
            self.native.response_ids.add(completion["response_id"])
            call["completion"] = completion
            self.owner.check()
            self.owner.save()
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "content-length": str(len(original_raw)),
                },
                content=original_raw,
                request=request,
            )
        except BaseException as error:
            call["error"] = error_info(error)
            self.owner.fail(error)
            raise
        finally:
            if phase is not None:
                phase.cancel()
            captured = b"".join(chunks)
            call.update(
                response_base64=base64.b64encode(captured).decode(),
                response_sha256=sha256(captured),
                captured_bytes=len(captured),
            )
            if response is not None:
                try:
                    await response.aclose()
                    call["response_closed"] = True
                except BaseException as error:
                    call["response_closed"] = False
                    self.owner.fail(error)
                    raise
            self.owner.save()

    async def aclose(self):
        if self.close_attempted:
            return
        self.close_attempted = True
        await self.inner.aclose()
        self.closed = True


class SyncTransport(httpx.BaseTransport):
    """Own actual sync pool while the normal application permits async calls only."""

    def __init__(self):
        self.inner = httpx.HTTPTransport(trust_env=False, retries=0)
        self.close_attempted = self.closed = False

    def handle_request(self, request):
        raise ValueError("normal owned allocation workflow requires async Gemini HTTP")

    def close(self):
        if self.close_attempted:
            return
        self.close_attempted = True
        self.inner.close()
        self.closed = True


class Native:
    def __init__(self, owner, configuration):
        self.owner, self.config = owner, configuration
        self.pending = None
        self.response_ids = set()
        self.consumed = set()
        self.transport = self.http = self.sync_transport = self.sync_http = (
            self.client
        ) = None
        self.close_attempted = self.closed = False
        try:
            from google import genai
            from google.genai import types

            self.transport = Transport(self)
            self.http = httpx.AsyncClient(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(300),
            )
            self.sync_transport = SyncTransport()
            self.sync_http = httpx.Client(
                transport=self.sync_transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(300),
            )
            self.client = genai.Client(
                api_key=configuration.api_key,
                vertexai=False,
                enterprise=False,
                http_options=types.HttpOptions(
                    base_url=configuration.base_url,
                    api_version="v1beta",
                    timeout=300000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                    httpx_client=self.sync_http,
                    httpx_async_client=self.http,
                ),
            )
        except BaseException as error:
            error.native_owner = self
            raise

    def prepare(self, role, llm_request):
        from google.genai import types

        self.owner.check()
        if (
            llm_request.model != self.config.model
            or llm_request.cache_config is not None
        ):
            raise ValueError(
                "original fixed Gemini model and no context cache required"
            )
        cfg = llm_request.config
        instruction = cfg.system_instruction
        if type(instruction) is str:
            instruction = types.Content(
                role="user", parts=[types.Part(text=instruction)]
            )
        tools = [
            t.model_dump(mode="json", by_alias=True, exclude_none=True)
            for t in cfg.tools or []
        ]
        # google-genai2.31 emits this public JSON-schema field in snake case,
        # although its Pydantic model uses the camel-case JSON alias.
        for tool in tools:
            for declaration in tool.get("functionDeclarations", []):
                for alias, wire in (
                    ("parametersJsonSchema", "parameters_json_schema"),
                    ("responseJsonSchema", "response_json_schema"),
                ):
                    if alias in declaration:
                        declaration[wire] = declaration.pop(alias)
        names = {f["name"] for t in tools for f in t.get("functionDeclarations", [])}
        if names != TOOLS[role]:
            raise ValueError("exact actual registered specialist tools required")
        expected = {
            "contents": [
                c.model_dump(mode="json", by_alias=True, exclude_none=True)
                for c in llm_request.contents
            ],
            "systemInstruction": instruction.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
            "tools": tools,
            "generationConfig": {
                "temperature": 0.0,
                "candidateCount": 1,
                "maxOutputTokens": 1024,
            },
        }
        self.pending = {"role": role, "sent": False, "expected": deepcopy(expected)}

    def verified_sdk(self, llm_response):
        self.owner.check()
        call = self.owner.receipt.data["calls"][-1]
        complete = call["completion"]
        if (
            llm_response.error_code is not None
            or llm_response.partial is True
            or llm_response.interrupted is True
            or llm_response.finish_reason != "STOP"
            or llm_response.model_version != complete["reported_model"]
            or llm_response.content is None
        ):
            raise ValueError("actual complete ADK Gemini response required")
        content = llm_response.content.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        if digest(content) != digest(complete["content"]):
            raise ValueError(
                "SDK normalization differs from whole original Gemini content"
            )
        call["sdk_response"] = llm_response.model_dump(mode="json", by_alias=True)
        call["sdk_verified"] = True
        self.owner.save()

    def consume(self, name, args):
        self.owner.check()
        call = self.owner.receipt.data["calls"][-1]
        f = call["completion"]["function"]
        if (
            not call.get("sdk_verified")
            or f is None
            or f["name"] != name
            or digest(f["args"]) != digest(args)
            or call["ordinal"] in self.consumed
        ):
            raise ValueError("genuine exact unused actual SDK role function required")
        self.consumed.add(call["ordinal"])
        return call["ordinal"]

    def provenance(self):
        self.owner.check()
        call = self.owner.receipt.data["calls"][-1]
        if not call.get("sdk_verified") or call["completion"]["final_text"] is None:
            raise ValueError("genuine complete actual SDK specialist final required")
        return {
            "kind": "owned_native_role",
            "provider": "gemini",
            "model": self.config.model,
            "request_sha256": call["request_sha256"],
            "response_sha256": call["response_sha256"],
        }

    async def close(self):
        if self.close_attempted:
            return
        self.close_attempted = True
        errors = []
        # google-genai explicitly leaves publicly supplied HTTPX clients open.
        # Close each SDK facade, then each separately owned supplied pool.
        if self.client is not None:
            try:
                await self.client.aio.aclose()
            except BaseException as error:
                errors.append(error)
        try:
            if self.http is not None:
                await self.http.aclose()
            elif self.transport is not None:
                await self.transport.aclose()
        except BaseException as error:
            errors.append(error)
        if self.client is not None:
            try:
                self.client.close()
            except BaseException as error:
                errors.append(error)
        try:
            if self.sync_http is not None:
                self.sync_http.close()
            elif self.sync_transport is not None:
                self.sync_transport.close()
        except BaseException as error:
            errors.append(error)
        self.closed = (self.transport is None or self.transport.closed) and (
            self.sync_transport is None or self.sync_transport.closed
        )
        if errors:
            raise errors[0]
