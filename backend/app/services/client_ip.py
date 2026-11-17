from ipaddress import ip_address, ip_network

from fastapi import HTTPException


def canonical_ip(value):
    if not isinstance(value, str) or "%" in value:
        raise ValueError("Invalid client address")
    address = ip_address(value)
    return getattr(address, "ipv4_mapped", None) or address


def client_ip(request, settings):
    """Trust Cloudflare's header only through an explicitly configured connector."""
    try:
        peer = canonical_ip(request.client.host if request.client else None)
        if settings.client_ip_source == "direct":
            return peer
        if not any(peer in ip_network(cidr) for cidr in settings.trusted_proxy_cidrs):
            raise ValueError("Untrusted connector")
        values = request.headers.getlist("cf-connecting-ip")
        if len(values) != 1:
            raise ValueError("Missing or repeated client header")
        return canonical_ip(values[0])
    except (ValueError, AttributeError):
        # Never fall back to the connector IP, X-Forwarded-For, or an exempt
        # internal address when the real client address cannot be verified.
        raise HTTPException(503, "접속 주소를 확인할 수 없습니다. 잠시 후 다시 시도하세요.") from None


def registration_group(address, settings):
    if any(address in ip_network(cidr) for cidr in settings.registration_exempt_cidrs):
        return None
    prefix = 32 if address.version == 4 else 64
    return str(ip_network(f"{address}/{prefix}", strict=False))
