import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.core.config import Settings
from backend.app.services.client_ip import client_ip, registration_group


def request(peer="198.51.100.10", headers=()):
    return Request({"type": "http", "method": "POST", "path": "/api/v1/auth/register",
                    "client": (peer, 50000), "headers": [(name.encode(), value.encode()) for name, value in headers]})


def test_direct_connections_ignore_all_forwarding_headers():
    settings = Settings()
    assert str(client_ip(request(headers=[("cf-connecting-ip", "127.0.0.1"),
                                         ("x-forwarded-for", "192.0.2.10")]), settings)) == "198.51.100.10"


@pytest.mark.parametrize("peer,headers", [
    ("198.51.100.11", [("cf-connecting-ip", "203.0.113.10")]),
    ("198.51.100.10", []),
    ("198.51.100.10", [("x-forwarded-for", "203.0.113.10")]),
    ("198.51.100.10", [("cf-connecting-ip", "203.0.113.10, 127.0.0.1")]),
    ("198.51.100.10", [("cf-connecting-ip", "203.0.113.10"), ("cf-connecting-ip", "127.0.0.1")]),
    ("198.51.100.10", [("cf-connecting-ip", "not-an-ip")]),
    ("198.51.100.10", [("cf-connecting-ip", "fe80::1%eth0")]),
])
def test_cloudflare_identity_fails_closed_without_trusted_peer_and_single_valid_header(peer, headers):
    settings = Settings(client_ip_source="cloudflare", trusted_proxy_cidrs=["198.51.100.10/32"],
                        registration_exempt_cidrs=["127.0.0.1/32"])
    with pytest.raises(HTTPException) as failure:
        client_ip(request(peer, headers), settings)
    assert failure.value.status_code == 503


def test_cloudflare_mode_needs_explicit_trusted_proxy():
    settings = Settings(client_ip_source="cloudflare")
    with pytest.raises(HTTPException):
        client_ip(request(headers=[("cf-connecting-ip", "203.0.113.10")]), settings)


def test_internal_networks_are_not_automatically_exempted():
    settings = Settings()
    address = client_ip(request("192.168.1.10"), settings)
    assert registration_group(address, settings) == "192.168.1.10/32"
    settings.registration_exempt_cidrs = ["192.168.1.0/24"]
    assert registration_group(address, settings) is None


@pytest.mark.parametrize("kwargs", [
    {"max_accounts_per_ip": 0}, {"client_ip_source": "forwarded"},
    {"trusted_proxy_cidrs": ["0.0.0.0/0"]}, {"registration_exempt_cidrs": ["::/0"]},
    {"trusted_proxy_cidrs": ["not-a-network"]},
])
def test_invalid_or_unrestricted_network_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)
