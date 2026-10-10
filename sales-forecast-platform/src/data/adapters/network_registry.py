"""Caller-owned registration; selecting a connector performs no network I/O."""
from src.data.adapters.base import SourceType
from src.data.adapters.network_common import NetworkAdapterError
from src.data.adapters.postgres_adapter import PostgreSQLAdapter
from src.data.adapters.rest_adapter import RESTAdapter


def network_connector_registry(asset, *, authorize_scope, postgres=None, rest=None,
                               resolve_postgres_credentials=None, resolve_rest_credentials=None,
                               postgres_connect=None, rest_transport_factory=None,
                               rest_resolver=None, rest_policy=None, rest_sleep=None):
    registry = {}
    if postgres is not None:
        def postgres_factory(selection):
            if selection.source_reference != postgres.source_reference:
                raise NetworkAdapterError("selection reference differs from configured source")
            return PostgreSQLAdapter(postgres, asset, authorize_scope=authorize_scope,
                resolve_credentials=resolve_postgres_credentials, connect=postgres_connect)
        registry[SourceType.POSTGRESQL] = postgres_factory
    if rest is not None:
        def rest_factory(selection):
            if selection.source_reference != rest.source_reference:
                raise NetworkAdapterError("selection reference differs from configured source")
            return RESTAdapter(rest, asset, authorize_scope=authorize_scope,
                resolve_credentials=resolve_rest_credentials, transport_factory=rest_transport_factory,
                resolver=rest_resolver, policy=rest_policy, sleep=rest_sleep)
        registry[SourceType.REST] = rest_factory
    return registry
