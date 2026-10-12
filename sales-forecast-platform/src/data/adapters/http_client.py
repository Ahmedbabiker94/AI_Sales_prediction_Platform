"""Shared address policy and bounded GET execution; callers own resource scope."""
from email.utils import parsedate_to_datetime
from datetime import timezone
import ipaddress
import math
import socket
from urllib.parse import urlsplit

from src.data.adapters.network_common import NetworkAdapterError


def _allowed_address(address, *, allow_loopback):
    effective = getattr(address, 'ipv4_mapped', None) or address
    if allow_loopback and effective.is_loopback:
        return True
    # Azure's platform virtual IP is classified as global by ipaddress.
    return (effective.is_global and not effective.is_multicast and not effective.is_unspecified
            and not effective.is_reserved and str(effective) != '168.63.129.16')


def _origin(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ('https', 'http') or not parts.hostname or parts.username is not None
                or parts.password is not None or '#' in url or '%' in parts.netloc
                or any(ord(c) < 33 or ord(c) > 126 for c in url) or '\\' in url):
            raise ValueError()
        host = parts.hostname.lower()
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        if not 1 <= port <= 65535:
            raise ValueError()
        return parts, (parts.scheme, host, port)
    except (ValueError, UnicodeError):
        raise NetworkAdapterError("invalid configured REST endpoint") from None


def resolve_destination(url, resolver, policy):
    _, (scheme, host, port) = _origin(url)
    try:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            effective = getattr(literal, 'ipv4_mapped', None) or literal
            if not _allowed_address(literal, allow_loopback=policy.allow_loopback_for_tests):
                raise ValueError()
            if scheme == 'http' and not effective.is_loopback:
                raise ValueError()
        addresses = resolver(host, port)
        if not addresses or len(addresses) > 16:
            raise ValueError()
        checked = []
        for value in addresses:
            if type(value) is not str or '%' in value:
                raise ValueError()
            address = ipaddress.ip_address(value)
            mapped = getattr(address, 'ipv4_mapped', None)
            effective = mapped or address
            if not _allowed_address(address, allow_loopback=policy.allow_loopback_for_tests):
                raise ValueError()
            if literal is not None and address != literal:
                raise ValueError()
            if scheme == 'http' and not effective.is_loopback:
                raise ValueError()
            checked.append(str(address))
        return checked[0]
    except Exception:
        raise NetworkAdapterError("REST destination resolution or address policy rejected") from None


def request_body(transport, *, limits, authorize, destination, build_headers, get_url, budget,
                 sleep, now, response_type, content_types=("application/json",),
                 max_response_bytes=None, on_success=None):
    cfg = limits
    byte_limit = cfg.max_response_bytes if max_response_bytes is None else max_response_bytes
    for attempt in range(cfg.max_retries + 1):
        authorize()
        address = destination()  # Recheck DNS for every attempt/page.
        headers = build_headers()  # Secrets resolved only after approval.
        url = get_url()
        if sum(len(k) + len(v) + 4 for k, v in headers.items()) + len(url) > cfg.max_request_bytes:
            raise NetworkAdapterError("REST request size limit exceeded")
        try:
            response = transport.request(url, address=address, headers=headers,
                connect_timeout=cfg.connect_timeout, read_timeout=cfg.read_timeout,
                max_response_bytes=byte_limit)
        except (TimeoutError, socket.timeout, ConnectionError):
            if attempt == cfg.max_retries:
                raise NetworkAdapterError("REST request timed out or connection failed") from None
            delay = min(2 ** attempt, cfg.max_retry_delay)
        else:
            if type(response) is not response_type or type(response.body) is not bytes:
                raise NetworkAdapterError("invalid REST transport response")
            if len(response.body) > byte_limit:
                raise NetworkAdapterError("REST response size limit exceeded")
            headers_map = {k.lower(): v for k, v in response.headers}
            if 300 <= response.status < 400:
                raise NetworkAdapterError("REST redirects are forbidden")
            if response.status == 200:
                if headers_map.get('content-type', '').split(';')[0].strip().lower() not in content_types:
                    raise NetworkAdapterError("REST response must be JSON")
                if headers_map.get('content-encoding', 'identity').lower() != 'identity':
                    raise NetworkAdapterError("compressed REST responses are unsupported")
                if on_success is not None:
                    on_success(response, headers_map)
                return response.body
            if response.status not in (429, 502, 503, 504) or attempt == cfg.max_retries:
                raise NetworkAdapterError("REST status rejected or retry budget exhausted")
            retry_after = headers_map.get('retry-after')
            delay = min(2 ** attempt, cfg.max_retry_delay)
            if retry_after is not None:
                try:
                    if retry_after.isdigit():
                        delay = int(retry_after)
                    else:
                        timestamp = parsedate_to_datetime(retry_after)
                        if timestamp.tzinfo is None:
                            raise ValueError()
                        delay = max(0, math.ceil((timestamp - now()).total_seconds()))
                except (ValueError, TypeError, OverflowError):
                    raise NetworkAdapterError("invalid REST retry timing") from None
        if delay > cfg.max_retry_delay or budget[0] + delay > cfg.max_total_retry_delay:
            raise NetworkAdapterError("REST rate limit exceeds configured retry delay budget")
        budget[0] += delay
        sleep(delay)
    raise NetworkAdapterError("REST retry budget exhausted")
