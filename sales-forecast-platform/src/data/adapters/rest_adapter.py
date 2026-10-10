"""Explicit GET-only JSON extraction with address-pinned HTTPS transport."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import base64
import http.client
import ipaddress
import json
import math
from queue import Queue, Empty
import socket
import ssl
from threading import BoundedSemaphore, Thread
import time
from urllib.parse import urlencode, urlsplit

from src.data.adapters.base import SourceType
from src.data.adapters.file_common import positive_integer, validate_headers
from src.data.adapters.json_adapter import _unique_object, _invalid_constant
from src.data.adapters.network_common import (
    NetworkAdapter, NetworkAdapterError, NetworkConfiguration, nonblank, record_reference,
)
from src.data.adapters.selection import SourceConfiguration
from src.data.contracts import DataFormat


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


@dataclass(frozen=True)
class RESTNetworkPolicy:
    """Trusted caller-only test exception; never inferred from source metadata."""
    allow_loopback_for_tests: bool = False
    allow_http_for_tests: bool = False


@dataclass(frozen=True)
class RESTCredentials:
    token: str | None = field(default=None, repr=False)
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class RESTConfiguration(NetworkConfiguration):
    endpoint: str = field(default='', repr=False)
    allowed_origins: tuple[str, ...] = field(default=(), repr=False)
    authentication: str = 'none'
    credential_reference: str | None = None
    records_key: str | None = None
    pagination: str = 'none'
    page_parameter: str = 'page'
    has_more_key: str = 'has_more'
    cursor_parameter: str = 'cursor'
    next_cursor_key: str = 'next_cursor'
    page_size_parameter: str = 'limit'
    page_size: int = 1000
    max_pages: int = 1000
    tenant_parameter: str | None = None
    connect_timeout: int = 10
    read_timeout: int = 10
    max_response_bytes: int = 4 * 1024 * 1024
    max_request_bytes: int = 16 * 1024
    max_cell_characters: int = 131072
    max_depth: int = 32
    max_retries: int = 2
    max_retry_delay: int = 30
    max_total_retry_delay: int = 60

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, 'allowed_origins', tuple(self.allowed_origins))

    def validate(self):
        super().validate()
        parts, origin = _origin(self.endpoint)
        if '?' in self.endpoint:
            raise NetworkAdapterError("endpoint queries must be built from explicit pagination/scope configuration")
        allowed = []
        for value in self.allowed_origins:
            parsed, item = _origin(value)
            if parsed.path not in ('', '/') or parsed.query:
                raise NetworkAdapterError("destination allowlist must contain origins only")
            allowed.append(item)
        if not allowed or origin not in allowed:
            raise NetworkAdapterError("REST destination is not allowlisted")
        if self.authentication not in ('none', 'bearer', 'basic'):
            raise NetworkAdapterError("unsupported REST authentication")
        if self.authentication == 'none':
            if self.credential_reference is not None:
                raise NetworkAdapterError("unused credential references are unsupported")
        else:
            nonblank(self.credential_reference)
            SourceConfiguration(self.credential_reference)
        if self.pagination not in ('none', 'page', 'cursor'):
            raise NetworkAdapterError("unsupported pagination mode")
        if self.pagination != 'none' and self.records_key is None:
            raise NetworkAdapterError("paginated responses require an explicit records key")
        for name in ('page_parameter', 'has_more_key', 'cursor_parameter', 'next_cursor_key', 'page_size_parameter'):
            nonblank(getattr(self, name))
        for name in ('records_key', 'tenant_parameter'):
            value = getattr(self, name)
            if value is not None:
                nonblank(value)
        parameter_names = [self.page_size_parameter]
        if self.pagination == 'page':
            parameter_names.append(self.page_parameter)
        elif self.pagination == 'cursor':
            parameter_names.append(self.cursor_parameter)
        if self.tenant_parameter:
            parameter_names.append(self.tenant_parameter)
        if len(set(parameter_names)) != len(parameter_names):
            raise NetworkAdapterError("conflicting REST query parameter roles")
        marker = self.has_more_key if self.pagination == 'page' else self.next_cursor_key
        if self.pagination != 'none' and self.records_key == marker:
            raise NetworkAdapterError("conflicting REST envelope fields")
        for name in ('page_size', 'max_pages', 'connect_timeout', 'read_timeout', 'max_response_bytes',
                     'max_request_bytes', 'max_cell_characters', 'max_depth', 'max_retry_delay', 'max_total_retry_delay'):
            positive_integer(getattr(self, name), name)
        if self.page_size > self.max_rows or self.max_pages > 10000 or self.connect_timeout > 60 or self.read_timeout > 60 or self.max_depth > 64:
            raise NetworkAdapterError("REST request or nesting bound exceeded")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 3:
            raise NetworkAdapterError("REST retries must be bounded between zero and three")
        if self.max_retry_delay > 60 or self.max_total_retry_delay > 180:
            raise NetworkAdapterError("REST retry delay exceeds allowed bounds")


@dataclass(frozen=True)
class RESTResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes = field(repr=False)


_dns_slots = BoundedSemaphore(4)


def resolve_addresses(host, port, *, timeout=10):
    """Bound caller wait and outstanding native DNS work; libc cannot be cancelled."""
    if not _dns_slots.acquire(blocking=False):
        raise NetworkAdapterError("REST DNS worker capacity exceeded")
    result = Queue(maxsize=1)
    def resolve():
        try:
            addresses = tuple(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
            result.put((True, addresses))
        except Exception:
            result.put((False, None))
        finally:
            _dns_slots.release()
    worker = Thread(target=resolve, daemon=True)
    try:
        worker.start()
    except Exception:
        _dns_slots.release()
        raise NetworkAdapterError("REST DNS worker could not start") from None
    try:
        success, addresses = result.get(timeout=timeout)
    except Empty:
        raise NetworkAdapterError("REST DNS resolution timed out") from None
    if not success:
        raise NetworkAdapterError("REST DNS resolution failed")
    return addresses


class _DeadlineReader:
    """Bound headers and total reads, including a peer trickling header bytes."""
    def __init__(self, reader, sock):
        self.reader, self.sock = reader, sock
        self.deadline = time.monotonic() + sock.gettimeout()
        self.line_bytes = 0

    def _timeout(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        self.sock.settimeout(remaining)

    def readline(self, limit=-1):
        output = bytearray()
        limit = min(limit if limit >= 0 else 8192, 8192)
        while len(output) < limit:
            self._timeout()
            byte = self.reader.read(1)
            if not byte:
                break
            output.extend(byte)
            self.line_bytes += 1
            if self.line_bytes > 65536:
                raise NetworkAdapterError("REST header size limit exceeded")
            if byte == b'\n':
                return bytes(output)
        if len(output) == limit:
            raise NetworkAdapterError("REST header line limit exceeded")
        return bytes(output)

    def read(self, count=-1):
        if count < 0:
            raise NetworkAdapterError("unbounded HTTP reads are unsupported")
        output = bytearray()
        while len(output) < count:
            self._timeout()
            chunk = self.reader.read1(count - len(output))
            if not chunk:
                break
            output.extend(chunk)
        return bytes(output)

    def read1(self, count):
        self._timeout()
        return self.reader.read1(count)

    def close(self):
        self.reader.close()


class _BoundedHTTPResponse(http.client.HTTPResponse):
    def __init__(self, sock, *args, **kwargs):
        super().__init__(sock, *args, **kwargs)
        self.fp = _DeadlineReader(self.fp, sock)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    response_class = _BoundedHTTPResponse

    def __init__(self, host, port, address, *, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Never reconnect by hostname after DNS authorization. TLS still checks
        # the original hostname (including SNI), not the pinned numeric address.
        family = socket.AF_INET6 if ipaddress.ip_address(self.address).version == 6 else socket.AF_INET
        raw = socket.socket(family, socket.SOCK_STREAM)
        try:
            raw.settimeout(self.timeout)
            raw.connect((self.address, self.port))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Used only when the caller explicitly enables controlled HTTP tests."""
    response_class = _BoundedHTTPResponse

    def __init__(self, host, port, address, *, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self):
        family = socket.AF_INET6 if ipaddress.ip_address(self.address).version == 6 else socket.AF_INET
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.address, self.port))


class PinnedHTTPTransport:
    """No proxy environment, cookies, automatic redirects or content decoding."""
    def __init__(self):
        self.closed = False
        self.connection = None

    def request(self, url, *, address, headers, connect_timeout, read_timeout, max_response_bytes):
        if self.closed:
            raise NetworkAdapterError("HTTP transport is closed")
        parts, origin = _origin(url)
        scheme, host, port = origin
        cls = _PinnedHTTPSConnection if scheme == 'https' else _PinnedHTTPConnection
        connection = self.connection = cls(host, port, address, timeout=connect_timeout)
        try:
            connection.connect()
            connection.sock.settimeout(read_timeout)
            target = parts.path or '/'
            if parts.query:
                target += '?' + parts.query
            connection.request('GET', target, headers=headers)
            response = connection.getresponse()
            try:
                response_headers = tuple(response.getheaders())
                header_map = {key.lower(): value for key, value in response_headers}
                if header_map.get('content-encoding', 'identity').lower() != 'identity':
                    raise NetworkAdapterError("compressed REST responses are unsupported")
                length = header_map.get('content-length')
                if length is not None and (not length.isdigit() or int(length) > max_response_bytes):
                    raise NetworkAdapterError("REST response size limit exceeded")
                body = bytearray()
                while True:
                    chunk = response.read1(min(65536, max_response_bytes + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > max_response_bytes:
                        raise NetworkAdapterError("REST response size limit exceeded")
                    if response.isclosed():
                        break
                if length is not None and len(body) != int(length):
                    raise NetworkAdapterError("truncated REST response")
                return RESTResponse(response.status, response_headers, bytes(body))
            finally:
                response.close()
        finally:
            connection.close()
            self.connection = None

    def close(self):
        self.closed = True
        if self.connection is not None:
            self.connection.close()
            self.connection = None


class RESTAdapter(NetworkAdapter):
    configuration_type = RESTConfiguration
    source_type = SourceType.REST
    source_format = DataFormat.API

    def __init__(self, configuration, asset, *, authorize_scope, resolve_credentials=None,
                 transport_factory=None, resolver=None, policy=None, sleep=None):
        super().__init__(configuration, asset, authorize_scope=authorize_scope)
        self.resolve_credentials = resolve_credentials
        self.transport_factory = transport_factory or PinnedHTTPTransport
        self.resolver = resolver or (lambda host, port: resolve_addresses(host, port, timeout=configuration.connect_timeout))
        self.policy = policy or RESTNetworkPolicy()
        self.sleep = sleep or time.sleep

    def validate_configuration(self):
        super().validate_configuration()
        if type(self.policy) is not RESTNetworkPolicy or any(type(v) is not bool for v in
                (self.policy.allow_http_for_tests, self.policy.allow_loopback_for_tests)):
            raise NetworkAdapterError("invalid trusted network policy")
        if (_origin(self.configuration.endpoint)[1][0] != 'https'
                and not (self.policy.allow_http_for_tests and self.policy.allow_loopback_for_tests)):
            raise NetworkAdapterError("HTTPS is required")
        if self.configuration.authentication != 'none' and not callable(self.resolve_credentials):
            raise NetworkAdapterError("explicit REST credential resolver is required")
        if not all(callable(v) for v in (self.transport_factory, self.resolver, self.sleep)):
            raise NetworkAdapterError("invalid explicit REST dependencies")

    def _destination(self):
        _, (scheme, host, port) = _origin(self.configuration.endpoint)
        try:
            try:
                literal = ipaddress.ip_address(host)
            except ValueError:
                literal = None
            if literal is not None:
                effective = getattr(literal, 'ipv4_mapped', None) or literal
                if not _allowed_address(literal, allow_loopback=self.policy.allow_loopback_for_tests):
                    raise ValueError()
                if scheme == 'http' and not effective.is_loopback:
                    raise ValueError()
            addresses = self.resolver(host, port)
            if not addresses or len(addresses) > 16:
                raise ValueError()
            checked = []
            for value in addresses:
                if type(value) is not str or '%' in value:
                    raise ValueError()
                address = ipaddress.ip_address(value)
                mapped = getattr(address, 'ipv4_mapped', None)
                effective = mapped or address
                if not _allowed_address(address, allow_loopback=self.policy.allow_loopback_for_tests):
                    raise ValueError()
                if literal is not None and address != literal:
                    raise ValueError()
                if scheme == 'http' and not effective.is_loopback:
                    raise ValueError()
                checked.append(str(address))
            return checked[0]
        except Exception:
            raise NetworkAdapterError("REST destination resolution or address policy rejected") from None

    def _headers(self):
        cfg = self.configuration
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity', 'User-Agent': 'CanonicalSourceConnector/1.0'}
        if cfg.authentication != 'none':
            credentials = self.resolve_credentials(cfg.credential_reference)
            if type(credentials) is not RESTCredentials:
                raise NetworkAdapterError("invalid REST credentials")
            if cfg.authentication == 'bearer':
                value = credentials.token
                if type(value) is not str or not value or len(value) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in value):
                    raise NetworkAdapterError("invalid bearer credential")
                headers['Authorization'] = 'Bearer ' + value
            else:
                username, password = credentials.username, credentials.password
                if (type(username) is not str or type(password) is not str or not username or not password
                        or ':' in username or len(username) + len(password) > 8192
                        or any(ord(c) < 32 for c in username + password)):
                    raise NetworkAdapterError("invalid basic credentials")
                headers['Authorization'] = 'Basic ' + base64.b64encode((username + ':' + password).encode('utf-8')).decode('ascii')
        return headers

    def _url(self, page, cursor):
        cfg = self.configuration
        parameters = {cfg.page_size_parameter: cfg.page_size}
        if cfg.tenant_parameter:
            parameters[cfg.tenant_parameter] = str(cfg.organization_id)
        if cfg.pagination == 'page':
            parameters[cfg.page_parameter] = page
        elif cfg.pagination == 'cursor' and cursor is not None:
            parameters[cfg.cursor_parameter] = cursor
        url = cfg.endpoint + '?' + urlencode(parameters)
        if len(url.encode('ascii')) > cfg.max_request_bytes:
            raise NetworkAdapterError("REST request size limit exceeded")
        return url

    def _response(self, transport, page, cursor, budget):
        cfg = self.configuration
        for attempt in range(cfg.max_retries + 1):
            self._authorize()
            address = self._destination()  # Recheck DNS for every attempt/page.
            headers = self._headers()  # Secrets resolved only after approval.
            url = self._url(page, cursor)
            if sum(len(k) + len(v) + 4 for k, v in headers.items()) + len(url) > cfg.max_request_bytes:
                raise NetworkAdapterError("REST request size limit exceeded")
            try:
                response = transport.request(url, address=address, headers=headers,
                    connect_timeout=cfg.connect_timeout, read_timeout=cfg.read_timeout,
                    max_response_bytes=cfg.max_response_bytes)
            except (TimeoutError, socket.timeout, ConnectionError):
                if attempt == cfg.max_retries:
                    raise NetworkAdapterError("REST request timed out or connection failed") from None
                delay = min(2 ** attempt, cfg.max_retry_delay)
            else:
                if type(response) is not RESTResponse or type(response.body) is not bytes:
                    raise NetworkAdapterError("invalid REST transport response")
                if len(response.body) > cfg.max_response_bytes:
                    raise NetworkAdapterError("REST response size limit exceeded")
                headers_map = {k.lower(): v for k, v in response.headers}
                if 300 <= response.status < 400:
                    raise NetworkAdapterError("REST redirects are forbidden")
                if response.status == 200:
                    if headers_map.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                        raise NetworkAdapterError("REST response must be JSON")
                    if headers_map.get('content-encoding', 'identity').lower() != 'identity':
                        raise NetworkAdapterError("compressed REST responses are unsupported")
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
                            delay = max(0, math.ceil((timestamp - datetime.now(timezone.utc)).total_seconds()))
                    except (ValueError, TypeError, OverflowError):
                        raise NetworkAdapterError("invalid REST retry timing") from None
            if delay > cfg.max_retry_delay or budget[0] + delay > cfg.max_total_retry_delay:
                raise NetworkAdapterError("REST rate limit exceeds configured retry delay budget")
            budget[0] += delay
            self.sleep(delay)
        raise NetworkAdapterError("REST retry budget exhausted")

    def _validate_json(self, value, depth=0):
        cfg = self.configuration
        if depth > cfg.max_depth:
            raise NetworkAdapterError("REST JSON nesting limit exceeded")
        if type(value) is dict:
            for key, item in value.items():
                if len(key) > cfg.max_cell_characters:
                    raise NetworkAdapterError("REST JSON key limit exceeded")
                self._validate_json(item, depth + 1)
        elif type(value) is list:
            for item in value:
                self._validate_json(item, depth + 1)
        elif type(value) is str:
            if len(value) > cfg.max_cell_characters:
                raise NetworkAdapterError("REST JSON cell limit exceeded")
        elif type(value) is float and not math.isfinite(value):
            raise NetworkAdapterError("REST JSON nonfinite numeric value")

    def _decode(self, body):
        cfg = self.configuration
        value = json.loads(body.decode('utf-8-sig'), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        self._validate_json(value)
        more, cursor = False, None
        if cfg.records_key is not None:
            fields = {cfg.records_key}
            if cfg.pagination == 'page':
                fields.add(cfg.has_more_key)
            elif cfg.pagination == 'cursor':
                fields.add(cfg.next_cursor_key)
            if type(value) is not dict or set(value) != fields:
                raise NetworkAdapterError("unsupported REST response envelope")
            if cfg.pagination == 'page':
                more = value[cfg.has_more_key]
                if type(more) is not bool:
                    raise NetworkAdapterError("REST page continuation must be boolean")
            elif cfg.pagination == 'cursor':
                cursor = value[cfg.next_cursor_key]
                if cursor is not None:
                    if type(cursor) is not str or not cursor.strip() or len(cursor) > 512:
                        raise NetworkAdapterError("invalid REST continuation token")
                    more = True
            value = value[cfg.records_key]
        if type(value) is not list or len(value) > cfg.page_size or any(type(row) is not dict for row in value):
            raise NetworkAdapterError("REST records must be a bounded array of objects")
        if not value:
            raise NetworkAdapterError("empty REST pages are unsupported")
        names = validate_headers(list(value[0]), cfg.max_columns)
        if any(set(row) != set(names) for row in value) or not set(cfg.record_key_fields) <= set(names):
            raise NetworkAdapterError("REST record fields are inconsistent or keys missing")
        return value, names, more, cursor

    def _pages(self):
        self._authorize()
        try:
            with self._owned(self.transport_factory()) as transport:
                page, cursor, seen_cursors, seen_ids, offset, names, budget = 1, None, set(), set(), 0, None, [0]
                while True:
                    rows, fields, more, next_cursor = self._decode(self._response(transport, page, cursor, budget))
                    if names is not None and fields != names:
                        raise NetworkAdapterError("REST schema changed between pages")
                    names = fields
                    if offset + len(rows) > self.configuration.max_rows:
                        raise NetworkAdapterError("REST total record limit exceeded")
                    ids = [record_reference([row[key] for key in self.configuration.record_key_fields]) for row in rows]
                    if len(set(ids)) != len(ids) or seen_ids.intersection(ids):
                        raise NetworkAdapterError("duplicate REST record references")
                    seen_ids.update(ids)
                    if next_cursor is not None:
                        if next_cursor in seen_cursors:
                            raise NetworkAdapterError("cyclic REST continuation token")
                        seen_cursors.add(next_cursor)
                    if more and page >= self.configuration.max_pages:
                        raise NetworkAdapterError("REST page limit exceeded")
                    yield rows, names, ids, offset, page
                    offset += len(rows)
                    if not more:
                        break
                    page += 1
                    cursor = next_cursor
        except NetworkAdapterError:
            raise
        except Exception:
            raise NetworkAdapterError("REST request, JSON parsing or extraction failed") from None

    def test_access(self):
        pages = self._pages()
        try:
            next(pages)
        finally:
            pages.close()

    def discover_schema(self):
        pages = self._pages()
        try:
            rows, names, *_ = next(pages)
            return {name: '|'.join(sorted({type(row[name]).__name__ for row in rows})) for name in names}
        finally:
            pages.close()

    def extract(self, *, batch_size=10000):
        with self._extraction(batch_size):
            pages = self._pages()
            try:
                for rows, names, ids, offset, page in pages:
                    for start in range(0, len(rows), batch_size):
                        if self.closed:
                            raise NetworkAdapterError("connector is closed")
                        chunk = rows[start:start + batch_size]
                        yield self._batch([[row[name] for name in names] for row in chunk], names,
                            ids[start:start + batch_size], offset + start, metadata={'source_page': page})
            finally:
                pages.close()
