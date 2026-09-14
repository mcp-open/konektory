from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_packeta.app import build_definition
from connector_packeta.service import READ_METHODS, PacketaService, parse_xml

API_PASSWORD = "0123456789abcdef0123456789abcdef"
API_KEY = "fedcba9876543210"
TRACKING = b"""<?xml version="1.0"?>
<response><status>ok</status><result>
<record><dateTime>2022-01-06T11:43:09</dateTime><statusCode>1</statusCode>
<statusText>Private Recipient handed over</statusText></record>
<record><dateTime>2022-01-07T12:44:43</dateTime><statusCode>7</statusCode>
<statusText>delivered</statusText></record>
</result></response>"""
CARRIERS = {
    "carriers": [
        {"id": 1, "name": "CZ Private Carrier HD", "country": "cz", "available": True},
        {"id": 2, "name": "SK Carrier HD", "country": "sk", "available": True},
        {"id": 3, "name": "CZ Other PP", "country": "cz", "available": False},
    ]
}


def context(**updates: Any) -> InvocationContext:
    values = {
        "api_password": API_PASSWORD,
        "api_key": API_KEY,
        "pii_key": "synthetic-pii-key-0123456789abcdef",
    }
    values.update(updates.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="user-1",
        workspace_id="ws-1",
        installation_id="inst-1",
        secret_ref="packeta/ws-1/inst-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=updates)


@pytest.mark.anyio
async def test_packet_tools_post_documented_xml_methods_only() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=TRACKING, headers={"Content-Type": "text/xml"})

    definition = build_definition(PacketaService(transport=httpx.MockTransport(upstream)))
    cases = {
        "packet_status": ({"packet_id": "Z1234567890"}, "packetStatus", "packetId"),
        "packet_tracking": ({"packet_id": "1234567890"}, "packetTracking", "packetId"),
        "packet_courier_tracking": (
            {"packet_id": "Z1234567890"},
            "packetCourierTracking",
            "packetId",
        ),
        "packet_info": ({"packet_id": "Z1234567890"}, "packetInfo", "packetId"),
        "packet_stored_until": ({"packet_id": "Z1234567890"}, "packetGetStoredUntil", "packetId"),
        "shipment_packets": ({"shipment_id": "D-123-XM-12345678"}, "shipmentPackets", "shipmentId"),
    }
    for name, (arguments, method, field) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "POST"
        assert str(request.url) == "https://www.zasilkovna.cz/api/rest"
        assert request.headers["content-type"].startswith("text/xml")
        assert request.headers["user-agent"].startswith("OpenMCP/")
        root = ET.fromstring(request.content)
        assert root.tag == method and method in READ_METHODS
        assert [child.tag for child in root] == ["apiPassword", field]
        assert root.find("apiPassword").text == API_PASSWORD  # type: ignore[union-attr]
        assert root.find(field).text == next(iter(arguments.values()))  # type: ignore[union-attr]
        text = result.model_dump_json()
        assert "Private Recipient" not in text and API_PASSWORD not in text
    assert set(definition.tools) == set(cases) | {"list_carriers"}
    assert all(request.url.path == "/api/rest" for request in seen)


@pytest.mark.anyio
async def test_carrier_feed_uses_api_key_path_and_filters_locally() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=CARRIERS)

    service = PacketaService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["list_carriers"]
    arguments = spec.input_model.model_validate({"country": "CZ", "lang": "sk", "limit": 1})
    result = await spec.handler(arguments, context())
    request = seen[-1]
    assert request.method == "GET"
    assert request.url.host == "pickup-point.api.packeta.com"
    assert request.url.path == f"/v5/{API_KEY}/carrier/json"
    assert request.url.params["lang"] == "sk"
    assert result.data["total"] == 2 and result.data["offset"] == 0
    assert len(result.data["items"]) == 1 and result.data["items"][0]["id"] == 1
    text = result.model_dump_json()
    assert "Private Carrier" not in text and API_KEY not in text
    assert API_KEY not in result.provenance.source_url
    assert await service.test_connection(context()) == {"connected": True}


@pytest.mark.anyio
async def test_bad_credentials_faults_and_broken_xml_fail_closed() -> None:
    calls = 0
    reply: dict[str, Any] = {"status": 200, "body": b"<response><status>fault</status>"
        b"<fault>IncorrectApiPasswordFault</fault><string>Private detail</string></response>"}

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(reply["status"], content=reply["body"])

    service = PacketaService(transport=httpx.MockTransport(upstream))
    for bad in ({"api_key": "short"}, {"api_password": "with space 0123456789abcdef"}):
        with pytest.raises(ConnectorError) as caught:
            await service.rest(context(credentials=bad), "packetStatus", {"packetId": "1"})
        assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    assert calls == 0
    with pytest.raises(ConnectorError) as caught:
        await service.rest(context(), "createPacket", {"packetId": "1"})
    assert calls == 0
    with pytest.raises(ConnectorError) as caught:
        await service.rest(context(), "packetStatus", {"packetId": "1"})
    assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    assert "Private detail" not in str(caught.value)
    reply["body"] = b"<response><status>fault</status><fault>PacketIdFault</fault></response>"
    with pytest.raises(ConnectorError) as caught:
        await service.rest(context(), "packetStatus", {"packetId": "1"})
    assert caught.value.code is ErrorCode.NOT_FOUND
    for body in (
        b"<html>Private error</html>",
        b"<!DOCTYPE x [<!ENTITY e 'x'>]><response><status>ok</status></response>",
        b"not xml at all",
    ):
        reply["body"] = body
        with pytest.raises(ConnectorError) as caught:
            await service.rest(context(), "packetStatus", {"packetId": "1"})
        assert caught.value.code is ErrorCode.UPSTREAM_ERROR
        assert "Private" not in str(caught.value)
    for status in (401, 404, 429, 503):
        reply["status"] = status
        with pytest.raises(ConnectorError):
            await service.rest(context(), "packetStatus", {"packetId": "1"})
    reply.update(status=200, body=b'{"carriers": "not a list"}')
    with pytest.raises(ConnectorError):
        await service.test_connection(context())


def test_xml_conversion_groups_repeated_elements() -> None:
    payload = parse_xml(TRACKING)
    assert payload["status"] == "ok"
    assert [record["statusCode"] for record in payload["result"]["record"]] == ["1", "7"]
    deep = b"<response>" + b"<a>" * 40 + b"</a>" * 40 + b"</response>"
    with pytest.raises(ConnectorError):
        parse_xml(deep)


@pytest.mark.parametrize(
    "arguments",
    [
        {"packet_id": "12345"},
        {"packet_id": "Z12345678901234567"},
        {"packet_id": "ABC1234567"},
        {"packet_id": 1234567890},
        {"packet_id": ""},
        {"packet_id": "Z1234567890", "url": "https://evil"},
        [],
    ],
)
def test_packet_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools["packet_status"].input_model.model_validate(arguments)


@pytest.mark.parametrize(
    "arguments",
    [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"country": "CZE"}, {"lang": "xx"}, {"name": ""}]
)
def test_carrier_arguments_are_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools["list_carriers"].input_model.model_validate(arguments)
