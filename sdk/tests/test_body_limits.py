from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from starlette.requests import Request

from openmcp_connector_runtime import ConnectorError, ErrorCode
from openmcp_connector_runtime.app import _MAX_REQUEST_BYTES, _body


def chunked_request(chunks: list[bytes]) -> Request:
    messages: AsyncIterator[dict[str, object]]

    async def receive_messages() -> AsyncIterator[dict[str, object]]:
        for index, chunk in enumerate(chunks):
            yield {
                "type": "http.request",
                "body": chunk,
                "more_body": index + 1 < len(chunks),
            }

    messages = receive_messages()

    async def receive() -> dict[str, object]:
        return await anext(messages)

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/v1/invoke",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


@pytest.mark.anyio
async def test_chunked_body_is_rejected_before_unbounded_buffering() -> None:
    request = chunked_request([b"x" * (_MAX_REQUEST_BYTES // 2), b"y" * 32769])

    with pytest.raises(ConnectorError) as caught:
        await _body(request)

    assert caught.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.anyio
async def test_chunked_body_at_limit_preserves_exact_bytes() -> None:
    raw = b'{"value":"' + b"x" * (_MAX_REQUEST_BYTES - 12) + b'"}'
    assert len(raw) == _MAX_REQUEST_BYTES
    parsed, received = await _body(chunked_request([raw[:8192], raw[8192:]]))

    assert received == raw
    assert len(parsed["value"]) == _MAX_REQUEST_BYTES - 12
