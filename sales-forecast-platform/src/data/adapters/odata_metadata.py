"""Bounded local OData 4.0 CSDL interpretation; never resolves references."""
from dataclasses import dataclass
from decimal import Decimal
from datetime import date
import hashlib
import re
from types import MappingProxyType

from defusedxml import ElementTree
import pandas as pd

from src.data.adapters.network_common import NetworkAdapterError


EDMX = 'http://docs.oasis-open.org/odata/ns/edmx'
EDM = 'http://docs.oasis-open.org/odata/ns/edm'
SCALARS = frozenset(('Edm.String', 'Edm.Boolean', 'Edm.Int32', 'Edm.Int64',
                     'Edm.Decimal', 'Edm.Date', 'Edm.DateTimeOffset'))


def identifier(value):
    if type(value) is not str or len(value) > 128 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise NetworkAdapterError('unsupported OData identifier')


def _facet(value, default, minimum, maximum):
    if value is None:
        return default
    if not re.fullmatch(r'[0-9]{1,3}', value) or not minimum <= int(value) <= maximum:
        raise NetworkAdapterError('unsupported OData scalar facet')
    return int(value)


@dataclass(frozen=True)
class Property:
    name: str
    edm_type: str
    nullable: bool
    precision: int | None = None
    scale: int | None = None
    max_length: int | None = None


@dataclass(frozen=True)
class EntitySchema:
    entity_type: str
    keys: tuple[str, ...]
    properties: object  # read-only property mapping, not another canonical model
    fingerprint: str


def parse_metadata(body, configuration):
    cfg = configuration
    if type(body) is not bytes or len(body) > cfg.max_metadata_bytes:
        raise NetworkAdapterError('OData metadata byte limit exceeded')
    try:
        root = ElementTree.fromstring(body, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        if root.tag != f'{{{EDMX}}}Edmx' or root.get('Version') != '4.0':
            raise NetworkAdapterError('only OData 4.0 XML metadata is supported')
        nodes, stack = 0, [(root, 0)]
        while stack:
            node, depth = stack.pop()
            nodes += 1
            if depth > cfg.max_metadata_depth or nodes > cfg.max_metadata_nodes:
                raise NetworkAdapterError('OData metadata resource limit exceeded')
            if node.tag == f'{{{EDMX}}}Reference':
                raise NetworkAdapterError('OData external metadata references are unsupported')
            stack.extend((child, depth + 1) for child in node)
        services = list(root)
        if len(services) != 1 or services[0].tag != f'{{{EDMX}}}DataServices':
            raise NetworkAdapterError('unsupported OData metadata structure')
        types, aliases, entity_sets, namespaces = {}, {}, [], set()
        schemas = []
        for schema in services[0]:
            if schema.tag != f'{{{EDM}}}Schema':
                raise NetworkAdapterError('unsupported OData schema structure')
            namespace = schema.get('Namespace', '')
            if not namespace or any(not part for part in namespace.split('.')):
                raise NetworkAdapterError('invalid OData schema namespace')
            for part in namespace.split('.'):
                identifier(part)
            if namespace in namespaces:
                raise NetworkAdapterError('duplicate OData schema namespace')
            namespaces.add(namespace)
            schemas.append((schema, namespace))
            alias = schema.get('Alias')
            if alias:
                identifier(alias)
                if alias in aliases:
                    raise NetworkAdapterError('ambiguous OData schema alias')
                aliases[alias] = namespace
        # Check the complete name table before interpreting any type references.
        # An alias may repeat its own namespace but cannot shadow another one.
        if any(alias in namespaces and alias != owner for alias, owner in aliases.items()):
            raise NetworkAdapterError('OData schema alias conflicts with namespace')
        for schema, namespace in schemas:
            for child in schema:
                if child.tag == f'{{{EDM}}}EntityType':
                    name = child.get('Name'); identifier(name)
                    qualified = namespace + '.' + name
                    if qualified in types:
                        raise NetworkAdapterError('duplicate OData entity type')
                    types[qualified] = child
                elif child.tag == f'{{{EDM}}}EntityContainer':
                    for entity in child:
                        if entity.tag == f'{{{EDM}}}EntitySet':
                            name = entity.get('Name'); identifier(name)
                            entity_sets.append((name, entity.get('EntityType', '')))
        resolved_sets = []
        for name, reference in entity_sets:
            prefix, separator, local = reference.rpartition('.')
            if not separator:
                raise NetworkAdapterError('invalid OData entity type reference')
            identifier(local)
            namespace = aliases.get(prefix, prefix)
            qualified = namespace + '.' + local
            if namespace not in namespaces or qualified not in types:
                raise NetworkAdapterError('unknown OData entity type reference')
            resolved_sets.append((name, qualified))
        matches = [target for name, target in resolved_sets if name == cfg.entity_set]
        if len(matches) != 1:
            raise NetworkAdapterError('approved OData entity set is absent or ambiguous')
        target = matches[0]
        entity = types.get(target)
        if entity is None or entity.get('BaseType') or entity.get('OpenType', 'false') != 'false' or entity.get('Abstract', 'false') != 'false':
            raise NetworkAdapterError('unsupported OData entity type')
        properties, keys = {}, []
        key_elements = entity.findall(f'{{{EDM}}}Key')
        if len(key_elements) != 1:
            raise NetworkAdapterError('explicit OData entity keys are required')
        for key in key_elements[0]:
            if key.tag != f'{{{EDM}}}PropertyRef' or key.get('Alias'):
                raise NetworkAdapterError('unsupported OData key reference')
            name = key.get('Name'); identifier(name); keys.append(name)
        if not keys or len(set(keys)) != len(keys) or set(keys) != set(cfg.record_key_fields):
            raise NetworkAdapterError('configured keys differ from OData entity keys')
        all_properties = entity.findall(f'{{{EDM}}}Property')
        if len(all_properties) > cfg.max_columns:
            raise NetworkAdapterError('OData property count limit exceeded')
        names = []
        for prop in all_properties:
            name = prop.get('Name'); identifier(name); names.append(name)
            if name not in cfg.projected_fields:
                continue
            kind, nullable = prop.get('Type'), prop.get('Nullable', 'true')
            if kind not in SCALARS or nullable not in ('true', 'false'):
                raise NetworkAdapterError('unsupported projected OData property')
            precision = scale = max_length = None
            if kind == 'Edm.Decimal':
                precision = _facet(prop.get('Precision'), 38, 1, 38)
                scale = _facet(prop.get('Scale'), 0, 0, precision)
            elif kind == 'Edm.DateTimeOffset':
                precision = _facet(prop.get('Precision'), 0, 0, 7)
            elif kind == 'Edm.String' and prop.get('MaxLength') is not None:
                value = prop.get('MaxLength')
                if not value.isdigit() or len(value) > 9 or not 1 <= int(value) <= cfg.max_cell_characters:
                    raise NetworkAdapterError('unsupported OData string length')
                max_length = int(value)
            if name in keys and (nullable != 'false' or kind == 'Edm.Boolean'):
                raise NetworkAdapterError('nullable or unsupported OData key type')
            properties[name] = Property(name, kind, nullable == 'true', precision, scale, max_length)
        if len(names) != len(set(names)) or set(properties) != set(cfg.projected_fields):
            raise NetworkAdapterError('projected OData properties are missing or ambiguous')
        fingerprint = hashlib.sha256(body).hexdigest()
        if cfg.expected_metadata_fingerprint is not None and fingerprint != cfg.expected_metadata_fingerprint:
            raise NetworkAdapterError('OData metadata fingerprint changed')
        return EntitySchema(target, tuple(sorted(keys)), MappingProxyType(properties), fingerprint)
    except NetworkAdapterError:
        raise
    except Exception:
        raise NetworkAdapterError('invalid or unsafe OData XML metadata') from None


def decode_scalar(value, prop, *, ieee754, max_characters):
    """Decode only metadata-declared scalars; no business or sign inference."""
    if value is None:
        if prop.nullable:
            return None
        raise NetworkAdapterError('null OData nonnullable property')
    kind = prop.edm_type
    if kind == 'Edm.String':
        if type(value) is str and len(value) <= (prop.max_length or max_characters):
            try:
                value.encode('utf-8')
            except UnicodeError:
                raise NetworkAdapterError('invalid OData string encoding') from None
            return value
    elif kind == 'Edm.Boolean':
        if type(value) is bool:
            return value
    elif kind in ('Edm.Int32', 'Edm.Int64'):
        bits = 32 if kind == 'Edm.Int32' else 64
        if kind == 'Edm.Int64' and ieee754:
            if type(value) is not str or len(value) > 20 or not re.fullmatch(r'-?(0|[1-9][0-9]*)', value):
                raise NetworkAdapterError('invalid OData Int64 representation')
            value = int(value)
        if type(value) is int and -(1 << (bits - 1)) <= value < (1 << (bits - 1)):
            return value
    elif kind == 'Edm.Decimal':
        if ieee754:
            if type(value) is not str or len(value) > max_characters or not re.fullmatch(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?', value):
                raise NetworkAdapterError('invalid OData Decimal representation')
            value = Decimal(value)
        elif type(value) is int:
            value = Decimal(value)
        if type(value) is Decimal and value.is_finite():
            _, digits, exponent = value.as_tuple()
            end = len(digits)
            while end and digits[end - 1] == 0:
                end -= 1
            if not end:
                return value
            exponent += len(digits) - end
            if max(0, -exponent) <= prop.scale and max(0, end + exponent) <= prop.precision - prop.scale:
                return value
    elif kind == 'Edm.Date':
        if type(value) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            return date.fromisoformat(value)
    elif kind == 'Edm.DateTimeOffset':
        if type(value) is str:
            match = re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.([0-9]{1,7}))?(Z|[+-][0-9]{2}:[0-9]{2})', value)
            if match and len(match.group(1) or '') <= prop.precision:
                offset = match.group(2)
                if offset == '-00:00':
                    raise NetworkAdapterError('ambiguous OData timezone offset')
                if offset != 'Z' and (int(offset[1:3]) > 14 or int(offset[4:]) > 59 or (offset[1:3] == '14' and offset[4:] != '00')):
                    raise NetworkAdapterError('invalid OData timezone offset')
                stamp = pd.Timestamp(value).as_unit('ns').tz_convert('UTC')
                return stamp
    raise NetworkAdapterError('OData value is incompatible with declared EDM type')
