-- Explicit controlled-offline migration. Never run at import/constructor/read.
CREATE SCHEMA canonical_forecast;
REVOKE ALL ON SCHEMA canonical_forecast FROM PUBLIC;

CREATE TABLE canonical_forecast.schema_migrations (
    version integer PRIMARY KEY CHECK (version = 1),
    checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);

CREATE FUNCTION canonical_forecast.ordered_uuid_set(a uuid[]) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog AS $$
    SELECT cardinality(a) BETWEEN 1 AND 32 AND array_ndims(a) = 1
       AND array_position(a, NULL) IS NULL
       AND a = ARRAY(SELECT DISTINCT s FROM unnest(a) s ORDER BY s)
$$;

CREATE TABLE canonical_forecast.batches (
    organization_id uuid NOT NULL,
    dataset_id uuid NOT NULL,
    idempotency_id text NOT NULL CHECK (idempotency_id ~ '^[0-9a-f]{64}$'),
    receipt_id uuid NOT NULL UNIQUE,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    record_set_canonical text NOT NULL CHECK (octet_length(record_set_canonical) BETWEEN 1 AND 524288),
    series_ids uuid[] NOT NULL CHECK (canonical_forecast.ordered_uuid_set(series_ids)),
    row_count integer NOT NULL CHECK (row_count BETWEEN 1 AND 32),
    transaction_timestamp timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (organization_id, dataset_id, idempotency_id),
    CHECK (content_sha256 = encode(sha256(convert_to(record_set_canonical, 'UTF8')), 'hex')),
    CHECK ((record_set_canonical::jsonb ->> 'version' = '1.0'
        AND record_set_canonical::jsonb ->> 'classification' = 'CONTENT_ONLY_NO_PRODUCER_OR_WRITE_AUTHORITY'
        AND record_set_canonical::jsonb ->> 'row_order' = 'declared_original_row_order_v1'
        AND record_set_canonical::jsonb ->> 'correction_policy' = 'INITIAL_WRITE_ONLY'
        AND record_set_canonical::jsonb ->> 'idempotency_id' = idempotency_id
        AND (record_set_canonical::jsonb #>> '{scope,organization_id}')::uuid = organization_id
        AND (record_set_canonical::jsonb #>> '{scope,dataset_id}')::uuid = dataset_id
        AND jsonb_array_length(record_set_canonical::jsonb -> 'rows') = row_count
        AND jsonb_array_length(record_set_canonical::jsonb #> '{scope,series_ids}') = row_count
        AND cardinality(series_ids) = row_count) IS TRUE)
);

CREATE TABLE canonical_forecast.evidence (
    organization_id uuid NOT NULL,
    dataset_id uuid NOT NULL,
    logical_key_id text NOT NULL CHECK (logical_key_id ~ '^[0-9a-f]{64}$'),
    revision integer NOT NULL CHECK (revision = 1),
    expected_current_version integer NOT NULL CHECK (expected_current_version = 0),
    idempotency_id text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 31),
    series_id uuid NOT NULL,
    candidate_id text NOT NULL CHECK (candidate_id ~ '^[0-9a-f]{64}$'),
    family text NOT NULL CHECK (family ~ '^[a-z][a-z0-9_]{0,63}$'),
    forecast_origin timestamptz NOT NULL,
    target_period timestamptz NOT NULL,
    target_period_end timestamptz NOT NULL,
    capture_cutoff timestamptz NOT NULL,
    key_canonical text NOT NULL CHECK (octet_length(key_canonical) BETWEEN 1 AND 16384),
    row_canonical text NOT NULL CHECK (octet_length(row_canonical) BETWEEN 1 AND 262144),
    row_sha256 text NOT NULL CHECK (row_sha256 ~ '^[0-9a-f]{64}$'),
    float32_le bytea NOT NULL CHECK (octet_length(float32_le) = 4),
    PRIMARY KEY (organization_id, dataset_id, logical_key_id, revision),
    UNIQUE (organization_id, dataset_id, idempotency_id, ordinal),
    UNIQUE (organization_id, dataset_id, logical_key_id, revision, idempotency_id, series_id, row_sha256),
    FOREIGN KEY (organization_id, dataset_id, idempotency_id)
        REFERENCES canonical_forecast.batches (organization_id, dataset_id, idempotency_id),
    CHECK (target_period = forecast_origin AND target_period_end > target_period AND capture_cutoff <= forecast_origin),
    CHECK (NOT ((get_byte(float32_le, 3) & 127) = 127 AND (get_byte(float32_le, 2) & 128) = 128)),
    CHECK (logical_key_id = encode(sha256(convert_to(key_canonical, 'UTF8')), 'hex')),
    CHECK (row_sha256 = encode(sha256(convert_to(row_canonical, 'UTF8')), 'hex')),
    CHECK ((jsonb_array_length(key_canonical::jsonb) = 14
        AND key_canonical::jsonb ->> 0 = 'canonical_candidate_h1_forecast_key_v1'
        AND (key_canonical::jsonb ->> 1)::uuid = organization_id
        AND (key_canonical::jsonb ->> 2)::uuid = dataset_id
        AND (key_canonical::jsonb ->> 3)::uuid = series_id
        AND key_canonical::jsonb ->> 4 = candidate_id
        AND key_canonical::jsonb ->> 5 = family
        AND (key_canonical::jsonb ->> 6)::timestamptz = forecast_origin
        AND (key_canonical::jsonb ->> 7)::timestamptz = target_period
        AND (key_canonical::jsonb ->> 8)::timestamptz = target_period_end
        AND (key_canonical::jsonb ->> 9)::integer = 1
        AND key_canonical::jsonb ->> 13 = 'identity_target_v1') IS TRUE),
    CHECK ((row_canonical::jsonb ->> 'version' = '1.0'
        AND (row_canonical::jsonb ->> 'revision')::integer = revision
        AND (row_canonical::jsonb ->> 'expected_current_version')::integer = expected_current_version
        AND row_canonical::jsonb ->> 'physical_dtype' = 'float32'
        AND row_canonical::jsonb ->> 'float32_le' = encode(float32_le, 'hex')
        AND (row_canonical::jsonb #>> '{key,organization_id}')::uuid = organization_id
        AND (row_canonical::jsonb #>> '{key,dataset_id}')::uuid = dataset_id
        AND (row_canonical::jsonb #>> '{key,series_id}')::uuid = series_id
        AND row_canonical::jsonb #>> '{key,candidate_id}' = candidate_id
        AND row_canonical::jsonb #>> '{key,family}' = family
        AND (row_canonical::jsonb #>> '{key,forecast_origin}')::timestamptz = forecast_origin
        AND (row_canonical::jsonb #>> '{key,target_period}')::timestamptz = target_period
        AND (row_canonical::jsonb #>> '{key,target_period_end}')::timestamptz = target_period_end
        AND (row_canonical::jsonb #>> '{key,horizon_step}')::integer = 1
        AND row_canonical::jsonb #>> '{key,transformation}' = 'identity_target_v1'
        AND (row_canonical::jsonb #>> '{lineage,capture_cutoff}')::timestamptz = capture_cutoff
        AND row_canonical::jsonb #>> '{key,target_metric}' = key_canonical::jsonb ->> 10
        AND (row_canonical::jsonb #>> '{key,measurement_json}')::jsonb = key_canonical::jsonb -> 11
        AND (row_canonical::jsonb #>> '{key,temporal_json}')::jsonb = key_canonical::jsonb -> 12) IS TRUE)
);

CREATE INDEX forecast_evidence_series_origin
ON canonical_forecast.evidence (organization_id, dataset_id, series_id, forecast_origin, logical_key_id);

CREATE TABLE canonical_forecast.current_records (
    organization_id uuid NOT NULL,
    dataset_id uuid NOT NULL,
    logical_key_id text NOT NULL,
    current_revision integer NOT NULL CHECK (current_revision = 1),
    idempotency_id text NOT NULL,
    series_id uuid NOT NULL,
    row_sha256 text NOT NULL,
    PRIMARY KEY (organization_id, dataset_id, logical_key_id),
    FOREIGN KEY (organization_id, dataset_id, logical_key_id, current_revision, idempotency_id, series_id, row_sha256)
        REFERENCES canonical_forecast.evidence
        (organization_id, dataset_id, logical_key_id, revision, idempotency_id, series_id, row_sha256)
);

CREATE FUNCTION canonical_forecast.immutable_evidence() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'canonical forecast evidence is immutable' USING ERRCODE = '23514';
END $$;

CREATE FUNCTION canonical_forecast.complete_batch() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, canonical_forecast AS $$
DECLARE
    b canonical_forecast.batches%ROWTYPE;
    n integer;
    pointers integer;
    series uuid[];
BEGIN
    SELECT * INTO b FROM canonical_forecast.batches
        WHERE organization_id=NEW.organization_id AND dataset_id=NEW.dataset_id AND idempotency_id=NEW.idempotency_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'batch unavailable' USING ERRCODE='23514';
    END IF;
    SELECT count(*), array_agg(series_id ORDER BY series_id) INTO n, series
        FROM canonical_forecast.evidence
        WHERE organization_id=b.organization_id AND dataset_id=b.dataset_id AND idempotency_id=b.idempotency_id;
    SELECT count(*) INTO pointers FROM canonical_forecast.current_records
        WHERE organization_id=b.organization_id AND dataset_id=b.dataset_id AND idempotency_id=b.idempotency_id;
    IF n <> b.row_count OR pointers <> b.row_count OR series IS DISTINCT FROM b.series_ids
        OR (SELECT array_agg(s::uuid ORDER BY s::uuid) FROM jsonb_array_elements_text(b.record_set_canonical::jsonb #> '{scope,series_ids}') s) IS DISTINCT FROM b.series_ids
        OR EXISTS (SELECT 1 FROM canonical_forecast.evidence e
            WHERE e.organization_id=b.organization_id AND e.dataset_id=b.dataset_id AND e.idempotency_id=b.idempotency_id
            AND (e.row_canonical::jsonb IS DISTINCT FROM (b.record_set_canonical::jsonb -> 'rows' -> e.ordinal)
              OR e.row_canonical::jsonb -> 'lineage' IS DISTINCT FROM (b.record_set_canonical::jsonb #> '{rows,0,lineage}')
              OR (e.candidate_id, e.family, e.forecast_origin, e.target_period, e.target_period_end) IS DISTINCT FROM
                 ((b.record_set_canonical::jsonb #>> '{rows,0,key,candidate_id}'),
                  (b.record_set_canonical::jsonb #>> '{rows,0,key,family}'),
                  (b.record_set_canonical::jsonb #>> '{rows,0,key,forecast_origin}')::timestamptz,
                  (b.record_set_canonical::jsonb #>> '{rows,0,key,target_period}')::timestamptz,
                  (b.record_set_canonical::jsonb #>> '{rows,0,key,target_period_end}')::timestamptz))) THEN
        RAISE EXCEPTION 'incomplete canonical forecast batch' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER immutable_batches BEFORE UPDATE OR DELETE OR TRUNCATE ON canonical_forecast.batches
    FOR EACH STATEMENT EXECUTE FUNCTION canonical_forecast.immutable_evidence();
CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE OR TRUNCATE ON canonical_forecast.evidence
    FOR EACH STATEMENT EXECUTE FUNCTION canonical_forecast.immutable_evidence();
CREATE TRIGGER immutable_current BEFORE UPDATE OR DELETE OR TRUNCATE ON canonical_forecast.current_records
    FOR EACH STATEMENT EXECUTE FUNCTION canonical_forecast.immutable_evidence();
CREATE TRIGGER immutable_migration BEFORE UPDATE OR DELETE OR TRUNCATE ON canonical_forecast.schema_migrations
    FOR EACH STATEMENT EXECUTE FUNCTION canonical_forecast.immutable_evidence();

CREATE CONSTRAINT TRIGGER complete_batches AFTER INSERT ON canonical_forecast.batches
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION canonical_forecast.complete_batch();
CREATE CONSTRAINT TRIGGER complete_rows AFTER INSERT ON canonical_forecast.evidence
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION canonical_forecast.complete_batch();
CREATE CONSTRAINT TRIGGER complete_current AFTER INSERT ON canonical_forecast.current_records
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION canonical_forecast.complete_batch();

ALTER TABLE canonical_forecast.batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical_forecast.batches FORCE ROW LEVEL SECURITY;
ALTER TABLE canonical_forecast.evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical_forecast.evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE canonical_forecast.current_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical_forecast.current_records FORCE ROW LEVEL SECURITY;

CREATE POLICY scoped_batches ON canonical_forecast.batches
    USING (organization_id = nullif(current_setting('r7_forecast.tenant', true), '')::uuid
       AND dataset_id = nullif(current_setting('r7_forecast.dataset', true), '')::uuid
       AND series_ids <@ string_to_array(current_setting('r7_forecast.series', true), ',')::uuid[]);
CREATE POLICY scoped_rows ON canonical_forecast.evidence
    USING (organization_id = nullif(current_setting('r7_forecast.tenant', true), '')::uuid
       AND dataset_id = nullif(current_setting('r7_forecast.dataset', true), '')::uuid
       AND series_id = ANY(string_to_array(current_setting('r7_forecast.series', true), ',')::uuid[]));
CREATE POLICY scoped_current ON canonical_forecast.current_records
    USING (organization_id = nullif(current_setting('r7_forecast.tenant', true), '')::uuid
       AND dataset_id = nullif(current_setting('r7_forecast.dataset', true), '')::uuid
       AND series_id = ANY(string_to_array(current_setting('r7_forecast.series', true), ',')::uuid[]));

GRANT USAGE ON SCHEMA canonical_forecast TO r7_forecast_writer, r7_forecast_reader;
GRANT SELECT ON canonical_forecast.schema_migrations TO r7_forecast_writer, r7_forecast_reader;
GRANT SELECT, INSERT ON canonical_forecast.batches, canonical_forecast.evidence, canonical_forecast.current_records TO r7_forecast_writer;
GRANT SELECT ON canonical_forecast.batches, canonical_forecast.evidence, canonical_forecast.current_records TO r7_forecast_reader;
REVOKE ALL ON FUNCTION canonical_forecast.ordered_uuid_set(uuid[]), canonical_forecast.immutable_evidence(), canonical_forecast.complete_batch() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION canonical_forecast.ordered_uuid_set(uuid[]), canonical_forecast.complete_batch() TO r7_forecast_writer, r7_forecast_reader;
