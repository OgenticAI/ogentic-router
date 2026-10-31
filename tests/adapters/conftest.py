"""Route the cloud SDKs' HTTP through pytest-httpx.

Recent ``openai`` / ``anthropic`` releases send requests through ``httpx2`` (a
separate package) instead of ``httpx``, so pytest-httpx — which patches
``httpx`` transports — no longer sees them and the mocked tests hit the real
API. When ``httpx2`` is installed and a test uses ``httpx_mock``, forward each
``httpx2`` request through an ``httpx`` transport (which pytest-httpx has
patched) and hand the mocked response back. Older SDKs that still use ``httpx``
are unaffected.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest


@pytest.fixture(autouse=True)
def _httpx2_through_httpx_mock(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if "httpx_mock" not in request.fixturenames:
        return
    try:
        import httpx2
    except ImportError:
        return

    async def handle(self: Any, req: Any) -> Any:
        body = await req.aread()
        resp = await httpx.AsyncHTTPTransport().handle_async_request(
            httpx.Request(req.method, str(req.url), headers=list(req.headers.raw), content=body)
        )
        return httpx2.Response(
            resp.status_code, headers=list(resp.headers.raw), content=await resp.aread(), request=req
        )

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", handle)
