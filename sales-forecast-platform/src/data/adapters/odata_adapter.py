"""Read-only, explicitly scoped OData 4.0 collections; not ERP business mapping."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import pandas as pd
import pyarrow as pa

from src.data.adapters.base import SourceType
from src.data.adapters.file_common import positive_integer, records_frame
from src.data.adapters.http_client import _origin, resolve_destination, request_body
from src.data.adapters.json_adapter import _unique_object, _invalid_constant
from src.data.adapters.network_common import NetworkAdapter, NetworkConfiguration, NetworkAdapterError, nonblank, record_reference
from src.data.adapters.odata_metadata import identifier, parse_metadata, decode_scalar
from src.data.adapters.rest_adapter import PinnedHTTPTransport, RESTCredentials, RESTNetworkPolicy, RESTResponse, resolve_addresses
from src.data.adapters.selection import SourceConfiguration
from src.data.contracts import DataFormat


@dataclass(frozen=True)
class ODataConfiguration(NetworkConfiguration):
    service_root: str = field(default='', repr=False)
    allowed_origins: tuple[str, ...] = field(default=(), repr=False)
    entity_set: str = ''
    projected_fields: tuple[str, ...] = ()
    order_by: tuple[str, ...] = ()  # ascending only; must include all entity keys
    scope_parameters: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    credential_reference: str = ''
    authorization_context: str = ''  # opaque trusted-policy lookup, not permission
    protocol_version: str = '4.0'
    expected_metadata_fingerprint: str | None = None
    page_size: int = 1000
    max_pages: int = 1000
    connect_timeout: int = 10
    read_timeout: int = 10
    max_response_bytes: int = 4 * 1024 * 1024
    max_request_bytes: int = 16 * 1024
    max_metadata_bytes: int = 2 * 1024 * 1024
    max_metadata_nodes: int = 10000
    max_metadata_depth: int = 32
    max_cell_characters: int = 131072
    max_retries: int = 2
    max_retry_delay: int = 30
    max_total_retry_delay: int = 60

    def __post_init__(self):
        super().__post_init__()
        for name in ('allowed_origins', 'projected_fields', 'order_by'):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, 'scope_parameters', tuple(tuple(pair) for pair in self.scope_parameters))

    def validate(self):
        super().validate()
        if type(self.service_root) is not str:
            raise NetworkAdapterError('invalid OData service root')
        parts, origin = _origin(self.service_root)
        if parts.query or '?' in self.service_root or not parts.path.endswith('/'):
            raise NetworkAdapterError('OData service root must be an explicit query-free directory URL')
        _safe_path(parts.path)
        allowed = []
        for item in self.allowed_origins:
            if type(item) is not str:
                raise NetworkAdapterError('invalid OData allowed origin')
            parsed, approved = _origin(item)
            if parsed.path not in ('', '/') or '?' in item:
                raise NetworkAdapterError('OData destination allowlist requires origins only')
            allowed.append(approved)
        if origin not in allowed:
            raise NetworkAdapterError('OData service origin is not approved')
        identifier(self.entity_set)
        for fields in (self.projected_fields, self.order_by):
            for name in fields:
                identifier(name)
            if not fields or len(fields) > self.max_columns or len(fields) != len(set(fields)):
                raise NetworkAdapterError('OData projected/order fields must be explicit, unique and bounded')
        if not set(self.record_key_fields) <= set(self.order_by) <= set(self.projected_fields):
            raise NetworkAdapterError('OData projection and ordering must retain entity keys')
        for key in self.record_key_fields:
            identifier(key)
        for pair in self.scope_parameters:
            if len(pair) != 2:
                raise NetworkAdapterError('invalid OData scope parameter')
            identifier(pair[0]); nonblank(pair[1])
        if len({pair[0] for pair in self.scope_parameters}) != len(self.scope_parameters):
            raise NetworkAdapterError('duplicate OData scope parameter')
        for value in (self.credential_reference, self.authorization_context):
            nonblank(value); SourceConfiguration(value)
        if self.protocol_version != '4.0':
            raise NetworkAdapterError('only OData 4.0 is supported')
        if self.expected_metadata_fingerprint is not None and (
                type(self.expected_metadata_fingerprint) is not str or not re.fullmatch(r'[0-9a-f]{64}', self.expected_metadata_fingerprint)):
            raise NetworkAdapterError('invalid approved OData metadata fingerprint')
        for name in ('page_size', 'max_pages', 'connect_timeout', 'read_timeout', 'max_response_bytes',
                     'max_request_bytes', 'max_metadata_bytes', 'max_metadata_nodes', 'max_metadata_depth',
                     'max_cell_characters', 'max_retry_delay', 'max_total_retry_delay'):
            positive_integer(getattr(self, name), name)
        if (self.page_size > self.max_rows or self.max_pages > 10000 or self.connect_timeout > 60
                or self.read_timeout > 60 or self.max_metadata_depth > 64):
            raise NetworkAdapterError('OData extraction bound exceeded')
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 3 or self.max_retry_delay > 60 or self.max_total_retry_delay > 180:
            raise NetworkAdapterError('OData retry bound exceeded')


def _safe_path(path):
    if '%' in path or '\\' in path or '//' in path or any(segment in ('.', '..') for segment in path.split('/')):
        raise NetworkAdapterError('ambiguous OData resource path')
    if not re.fullmatch(r'/[A-Za-z0-9_./~-]*', path):
        raise NetworkAdapterError('unsupported OData resource path')


class ODataAdapter(NetworkAdapter):
    configuration_type = ODataConfiguration
    source_type = SourceType.ODATA
    source_format = DataFormat.API

    def __init__(self, configuration, asset, *, authorize_scope, authorize_request,
                 resolve_credentials, transport_factory=None, resolver=None, policy=None, sleep=None):
        super().__init__(configuration, asset, authorize_scope=authorize_scope)
        self.authorize_request = authorize_request
        self.resolve_credentials = resolve_credentials
        self.transport_factory = transport_factory or PinnedHTTPTransport
        self.resolver = resolver or (lambda host, port: resolve_addresses(host, port, timeout=configuration.connect_timeout))
        self.policy = policy or RESTNetworkPolicy()
        self.sleep = sleep or time.sleep

    def validate_configuration(self):
        super().validate_configuration()
        if type(self.policy) is not RESTNetworkPolicy or any(type(v) is not bool for v in
                (self.policy.allow_loopback_for_tests, self.policy.allow_http_for_tests)):
            raise NetworkAdapterError('invalid trusted OData network policy')
        scheme = _origin(self.configuration.service_root)[1][0]
        if scheme != 'https' and not (self.policy.allow_http_for_tests and self.policy.allow_loopback_for_tests):
            raise NetworkAdapterError('HTTPS is required')
        if not all(callable(fn) for fn in (self.authorize_request, self.resolve_credentials, self.transport_factory, self.resolver, self.sleep)):
            raise NetworkAdapterError('explicit trusted OData dependencies are required')

    def _fixed_query(self):
        cfg = self.configuration
        return {**dict(cfg.scope_parameters), '$select': ','.join(cfg.projected_fields),
                '$orderby': ','.join(name + ' asc' for name in cfg.order_by), '$top': str(cfg.page_size)}

    def _entity_url(self):
        return self.configuration.service_root + self.configuration.entity_set

    def _scope(self, url, purpose):
        cfg = self.configuration
        parsed, origin = _origin(url)
        _safe_path(parsed.path[:-len('$metadata')] if purpose == 'metadata' and parsed.path.endswith('$metadata') else parsed.path)
        root, approved = _origin(cfg.service_root)
        expected = root.path + ('$metadata' if purpose == 'metadata' else cfg.entity_set)
        # Metadata's fixed $ segment is the sole permitted path exception.
        if origin != approved or parsed.path != expected:
            raise NetworkAdapterError('OData request escapes authorized service resource')
        if purpose == 'metadata' and parsed.query:
            raise NetworkAdapterError('OData metadata query is unsupported')
        if len(url.encode('ascii')) > cfg.max_request_bytes:
            raise NetworkAdapterError('OData request size limit exceeded')

    def _next_url(self, link, current):
        cfg = self.configuration
        if type(link) is not str or not link or len(link) > cfg.max_request_bytes or link.startswith('//'):
            raise NetworkAdapterError('invalid OData continuation link')
        if any(ord(c) < 33 or ord(c) > 126 for c in link):
            raise NetworkAdapterError('invalid OData continuation encoding')
        raw = urlsplit(link)
        _safe_path(raw.path if raw.path.startswith('/') else '/' + raw.path)
        if raw.fragment or '#' in link or re.search(r'%(?![0-9A-Fa-f]{2})', link):
            raise NetworkAdapterError('ambiguous OData continuation link')
        url = urljoin(current, link)
        self._scope(url, 'entity')
        try:
            pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True, strict_parsing=True,
                              encoding='utf-8', errors='strict')
        except UnicodeError:
            raise NetworkAdapterError('invalid UTF-8 OData continuation query') from None
        query = dict(pairs)
        fixed = self._fixed_query()
        if len(pairs) != len(query) or any(query.get(name) != value for name, value in fixed.items()):
            raise NetworkAdapterError('OData continuation changes extraction or tenant scope')
        extras = set(query) - set(fixed)
        if extras not in ({'$skiptoken'}, {'$skip'}):
            raise NetworkAdapterError('unsupported OData continuation parameters')
        token = query[next(iter(extras))]
        if not token or len(token) > 2048 or any(ord(c) < 32 for c in token):
            raise NetworkAdapterError('invalid OData continuation token')
        if '$skip' in extras and (not re.fullmatch(r'[0-9]{1,10}', token) or int(token) > cfg.max_rows):
            raise NetworkAdapterError('invalid OData skip offset')
        return self._entity_url() + '?' + urlencode(sorted(query.items()))

    def _request(self, transport, url, purpose, budget):
        cfg = self.configuration
        self._scope(url, purpose)
        def authorize():
            self._authorize()
            self._scope(url, purpose)
            authorization_asset = self.asset.model_copy(deep=True)
            try:
                allowed = self.authorize_request(cfg, authorization_asset, purpose)
            except Exception:
                raise NetworkAdapterError('OData request authorization failed') from None
            if allowed is not True:
                raise NetworkAdapterError('OData request purpose is not authorized')
        def headers():
            try:
                credentials = self.resolve_credentials(cfg.credential_reference, organization_id=cfg.organization_id,
                    authorization_context=cfg.authorization_context, purpose=purpose)
            except Exception:
                raise NetworkAdapterError('OData credential resolution failed') from None
            if type(credentials) is not RESTCredentials:
                raise NetworkAdapterError('invalid OData Bearer credentials')
            token = credentials.token
            if type(token) is not str or not token or len(token) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise NetworkAdapterError('invalid OData Bearer credential')
            return {'Authorization': 'Bearer ' + token, 'Accept-Encoding': 'identity',
                    'Accept': 'application/xml' if purpose == 'metadata' else 'application/json;IEEE754Compatible=true',
                    'OData-Version': '4.0', 'OData-MaxVersion': '4.0'}
        observed = {}
        def protocol(response, response_headers):
            names = [name.lower() for name, _ in response.headers]
            if any(names.count(name) > 1 for name in ('content-type', 'odata-version', 'content-encoding')):
                raise NetworkAdapterError('ambiguous OData response headers')
            if response_headers.get('odata-version', '').strip() != cfg.protocol_version:
                raise NetworkAdapterError('OData response version is incompatible')
            media = response_headers.get('content-type', '').split(';')[1:]
            parameters = {}
            for part in media:
                key, separator, value = part.strip().partition('=')
                if not separator or key.lower() in parameters:
                    raise NetworkAdapterError('invalid OData media parameters')
                if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                    value = value[1:-1]
                if not re.fullmatch(r'[A-Za-z0-9_.-]+', key) or not re.fullmatch(r'[A-Za-z0-9_.-]+', value):
                    raise NetworkAdapterError('invalid OData media parameters')
                parameters[key.lower()] = value.lower()
            ieee = parameters.get('ieee754compatible', 'false')
            if ieee not in ('true', 'false'):
                raise NetworkAdapterError('unsupported OData numeric wire format')
            if parameters.get('charset', 'utf-8') not in ('utf-8', 'utf8'):
                raise NetworkAdapterError('unsupported OData character encoding')
            observed['ieee754'] = ieee == 'true'
        body = request_body(transport, limits=cfg, authorize=authorize,
            destination=lambda: resolve_destination(url, self.resolver, self.policy), build_headers=headers,
            get_url=lambda: url, budget=budget, sleep=self.sleep, now=lambda: datetime.now(timezone.utc),
            response_type=RESTResponse, content_types=('application/xml', 'text/xml') if purpose == 'metadata' else ('application/json',),
            max_response_bytes=cfg.max_metadata_bytes if purpose == 'metadata' else cfg.max_response_bytes, on_success=protocol)
        return body, observed['ieee754']

    def _metadata(self, transport, budget):
        body, _ = self._request(transport, self.configuration.service_root + '$metadata', 'metadata', budget)
        return parse_metadata(body, self.configuration)

    def _decode(self, body, schema, ieee):
        cfg = self.configuration
        value = json.loads(body.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant, parse_float=Decimal)
        if type(value) is not dict or not {'value', '@odata.context'} <= set(value) or set(value) - {'value', '@odata.context', '@odata.nextLink', '@odata.count'}:
            raise NetworkAdapterError('unsupported OData V4 collection envelope')
        context = value['@odata.context']
        expected = cfg.service_root + '$metadata#' + cfg.entity_set
        if context != expected and context != expected + '(' + ','.join(cfg.projected_fields) + ')':
            raise NetworkAdapterError('OData collection context changed')
        if '@odata.count' in value and (type(value['@odata.count']) is not int or value['@odata.count'] < 0):
            raise NetworkAdapterError('invalid OData count annotation')
        rows = value['value']
        if '@odata.nextLink' in value and type(value['@odata.nextLink']) is not str:
            raise NetworkAdapterError('invalid OData nextLink annotation')
        if type(rows) is not list or len(rows) > cfg.page_size:
            raise NetworkAdapterError('OData collection page limit exceeded')
        decoded = []
        for row in rows:
            if type(row) is not dict or set(row) != set(cfg.projected_fields):
                raise NetworkAdapterError('OData projected fields are missing or changed')
            decoded.append([decode_scalar(row[name], schema.properties[name], ieee754=ieee,
                                           max_characters=cfg.max_cell_characters) for name in cfg.projected_fields])
        return decoded, value.get('@odata.nextLink')

    def test_access(self):
        self.discover_schema()

    def discover_schema(self):
        self._authorize()
        try:
            with self._owned(self.transport_factory()) as transport:
                schema = self._metadata(transport, [0])
                return {name: schema.properties[name].edm_type for name in self.configuration.projected_fields}
        except NetworkAdapterError:
            raise
        except Exception:
            raise NetworkAdapterError('OData discovery or authorization failed') from None

    def extract(self, *, batch_size=10000):
        with self._extraction(batch_size):
            try:
                with self._owned(self.transport_factory()) as transport:
                    cfg, budget = self.configuration, [0]
                    schema = self._metadata(transport, budget)
                    url = self._entity_url() + '?' + urlencode(sorted(self._fixed_query().items()))
                    visited, seen, offset, page = set(), set(), 0, 0
                    while True:
                        if url in visited or page >= cfg.max_pages:
                            raise NetworkAdapterError('cyclic OData pagination or page limit exceeded')
                        visited.add(url); page += 1
                        body, ieee = self._request(transport, url, 'entity', budget)
                        rows, link = self._decode(body, schema, ieee)
                        if offset + len(rows) > cfg.max_rows or not rows:
                            raise NetworkAdapterError('empty OData page or total row limit exceeded')
                        next_url = self._next_url(link, url) if link is not None else None
                        if next_url is not None and (next_url in visited or page >= cfg.max_pages):
                            raise NetworkAdapterError('cyclic OData pagination or page limit exceeded')
                        indexes = [cfg.projected_fields.index(key) for key in schema.keys]
                        ids = []
                        for row in rows:
                            identity = [cfg.entity_set]
                            for key, index in zip(schema.keys, indexes):
                                cell = row[index]
                                if cell is None:
                                    raise NetworkAdapterError('missing OData entity key')
                                if type(cell) is pd.Timestamp:
                                    cell = cell.isoformat()
                                identity.extend((key, schema.properties[key].edm_type, cell))
                            ids.append(record_reference(identity))
                        if len(set(ids)) != len(ids) or seen.intersection(ids):
                            raise NetworkAdapterError('duplicate OData entity keys')
                        seen.update(ids)
                        for start in range(0, len(rows), batch_size):
                            frame = records_frame(rows[start:start + batch_size], cfg.projected_fields)
                            for name in cfg.projected_fields:
                                prop = schema.properties[name]
                                if prop.edm_type == 'Edm.Decimal':
                                    array = pa.array(frame[name].tolist(), type=pa.decimal128(prop.precision, prop.scale))
                                    frame[name] = pd.Series(array, dtype=pd.ArrowDtype(array.type))
                                elif prop.edm_type == 'Edm.DateTimeOffset':
                                    frame[name] = pd.to_datetime(frame[name], utc=True)
                            yield self._batch(frame, cfg.projected_fields, ids[start:start + batch_size], offset + start,
                                metadata={'odata_schema_fingerprint': schema.fingerprint, 'odata_version': cfg.protocol_version,
                                          'source_page': page, 'source_consistency': 'upstream_snapshot_unverified'})
                        offset += len(rows)
                        if next_url is None:
                            break
                        url = next_url
            except NetworkAdapterError:
                raise
            except Exception:
                raise NetworkAdapterError('OData request, decoding or extraction failed') from None
