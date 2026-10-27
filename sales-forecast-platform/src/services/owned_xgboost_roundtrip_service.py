"""Original M6 owner bridge, B1 only. No paths, store, runtime snapshot or M7 use.

Trusted bootstrap supplies the real M6 trainer and synthetic validation authority.
Python private attributes are not a sandbox. Native calls require an externally
isolated subprocess; in-process clocks/cancellation are cooperative only.
"""
import hashlib
import importlib
import json
import math
from threading import Lock
from time import monotonic
import numpy as np
from src.data.contracts.model_candidate import strict_float32
from src.data.contracts.native_xgboost_roundtrip import (
    NativeRoundTripLimits, NativeXGBoostManifest, NativeArtifactEnvelope, NativeRoundTripReport,
)
from src.data.contracts.training_adapter import TrainingCandidateMetadata
from src.preprocessing.canonical_tabular import TabularOutput, TabularPreprocessingState
from src.services.canonical_adapter_training_service import CanonicalAdapterTrainingService, CandidateReference
from src.training.canonical_adapters import NativeBackend


class NativeRoundTripError(ValueError):
    pass


def _reject():
    error = NativeRoundTripError('owned synthetic native round-trip unavailable or denied')
    try:
        raise error from None
    finally:
        error.__cause__ = None
        error.__context__ = None


def _framework():
    xgb = importlib.import_module('xgboost')
    if xgb.__version__ != '3.2.0':
        raise ValueError()
    return xgb


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


class OwnedXGBoostRoundTripService:
    def __init__(self, trainer, *, authorize, limits=None, elapsed_clock=monotonic):
        try:
            if (type(trainer) is not CanonicalAdapterTrainingService
                    or trainer._backend_factory is not NativeBackend
                    or not callable(authorize) or not callable(elapsed_clock)):
                raise ValueError()
            limits = NativeRoundTripLimits() if limits is None else NativeRoundTripLimits.model_validate_json(limits.canonical_json)
            self._trainer = trainer
            self._authorize = authorize
            self._limits = limits
            self._clock = elapsed_clock
            self._lock = Lock()
            self._issued = {}
            self._self = self
            return
        except Exception:
            pass
        _reject()

    def _owned(self, reference):
        if self._self is not self or type(reference) is not CandidateReference:
            raise ValueError()
        record = self._trainer._candidates.get(id(reference))
        if (record is None or record[0] is not self._trainer or record[1] is not reference
                or self._trainer._backend_factory is not NativeBackend):
            raise ValueError()
        metadata = TrainingCandidateMetadata.model_validate_json(record[2].canonical_json)
        state = TabularPreprocessingState.model_validate_json(record[4].canonical_json)
        if (metadata.specification.family != 'xgboost' or metadata.backend != 'native_framework'
                or metadata.backend_version != '3.2.0' or metadata.specification.input_dtype != 'float32'
                or metadata.fitted_state_id != state.state_id or metadata.plan_digest != state.plan.digest
                or metadata.input_geometry[0][1] != len(state.feature_schema.features)):
            raise ValueError()
        return record, metadata, state

    def _checkpoint(self, reference, cancelled):
        if not callable(cancelled):
            raise ValueError()
        previous = None
        start = None
        pinned = None
        def check():
            nonlocal previous, start, pinned
            # All callbacks finish before the live owner lookup.
            stop = cancelled()
            now = self._clock()
            if type(now) not in (int, float) or not math.isfinite(now):
                raise ValueError()
            if start is None:
                start = now
            if stop is not False or previous is not None and now < previous or now-start >= self._limits.max_elapsed_seconds:
                raise ValueError()
            previous = now
            owned = self._owned(reference)
            if pinned is None:
                pinned = owned[0]
            if owned[0] is not pinned:
                raise ValueError()
            return owned
        return check

    def _operation(self, reference, cancelled, action):
        if self._self is not self or not self._lock.acquire(blocking=False):
            _reject()
        locked = False
        try:
            locked = self._trainer._lock.acquire(blocking=False)
            if not locked:
                raise ValueError()
            check = self._checkpoint(reference, cancelled)
            return action(check)
        except Exception:
            pass
        finally:
            if locked:
                self._trainer._lock.release()
            self._lock.release()
        _reject()

    def _approve(self, metadata, stage, artifact_id, validation_id, check):
        if self._authorize(metadata, stage, artifact_id, validation_id) is not True:
            raise ValueError()
        check()

    def export(self, reference, *, cancelled=lambda: False):
        def action(check):
            record, metadata, state = check()
            self._approve(metadata, 'export', None, None, check)
            xgb = _framework()
            if type(record[3]) is not xgb.XGBRegressor:
                raise ValueError()
            model = record[3]
            if model.__sklearn_is_fitted__() is not True:
                raise ValueError()
            booster = model.get_booster()
            names = tuple(f.name for f in state.feature_schema.features)
            if (booster.num_features() != len(names) or booster.num_boosted_rounds() != metadata.specification.steps
                    or json.loads(booster.save_config())['learner']['objective']['name'] != 'reg:squarederror'):
                raise ValueError()
            # M6 trained positional arrays. Bind names on a private native copy;
            # never mutate the original fitted model or accepted preprocessing.
            detached = booster.copy()
            detached.feature_names = list(names)
            detached.set_attr(b1_candidate=metadata.candidate_id, b1_state=state.state_id,
                              b1_training_input=metadata.input_digest)
            payload = bytes(detached.save_raw(raw_format='ubj'))
            check()
            if not 1 <= len(payload) <= self._limits.max_artifact_bytes:
                raise ValueError()
            manifest = NativeXGBoostManifest(training=metadata, frozen_state_id=state.state_id,
                feature_schema=state.feature_schema, native_attributes=tuple(sorted(detached.attributes().items())),
                payload_size=len(payload), payload_sha256=_sha(payload))
            envelope = NativeArtifactEnvelope(manifest, payload)
            self._approve(metadata, 'export_return', manifest.artifact_id, None, check)
            if check()[0] is not record or len(self._issued) >= 8:
                raise ValueError()
            self._issued[id(envelope)] = (envelope, record, manifest.canonical_json, payload)
            return envelope
        return self._operation(reference, cancelled, action)

    def verify(self, reference, envelope, validation, *, cancelled=lambda: False):
        def action(check):
            record, metadata, state = check()
            # Copies, reconstructed envelopes and arbitrary files/bytes never
            # reach the native parser, even with coherently rewritten digests.
            issued = self._issued.get(id(envelope))
            if (type(envelope) is not NativeArtifactEnvelope or issued is None
                    or issued[0] is not envelope or issued[1] is not record
                    or envelope.manifest.canonical_json != issued[2] or envelope.payload != issued[3]):
                raise ValueError()
            m = NativeXGBoostManifest.model_validate_json(envelope.manifest.canonical_json)
            if (type(envelope.payload) is not bytes or len(envelope.payload) != m.payload_size
                    or len(envelope.payload) > self._limits.max_artifact_bytes or _sha(envelope.payload) != m.payload_sha256
                    or m.training != metadata or m.frozen_state_id != state.state_id or m.feature_schema != state.feature_schema):
                raise ValueError()
            if type(validation) is not TabularOutput:
                raise ValueError()
            output = TabularOutput.model_validate_json(validation.canonical_json)
            r = output.representation
            if (output.model_family != 'xgboost' or output.state != state or r.binding.partition != 'validation'
                    or r.compatibility_id != metadata.representation_compatibility_id
                    or len(output.values) > self._limits.max_rows
                    or len(output.values)*len(state.feature_schema.features) > self._limits.max_cells):
                raise ValueError()
            matrix = strict_float32(output.values)
            if matrix.ndim != 2 or matrix.shape != (len(output.values), len(state.feature_schema.features)):
                raise ValueError()
            validation_id = output.output_id
            self._approve(metadata, 'load_validation', m.artifact_id, validation_id, check)
            xgb = _framework()
            loaded = xgb.Booster(params={'nthread': 1})
            loaded.load_model(bytearray(envelope.payload))
            check()
            names = tuple(f.name for f in state.feature_schema.features)
            if (tuple(loaded.feature_names or ()) != names or loaded.num_features() != len(names)
                    or loaded.num_boosted_rounds() != metadata.specification.steps
                    or tuple(sorted(loaded.attributes().items())) != m.native_attributes
                    or json.loads(loaded.save_config())['learner']['objective']['name'] != 'reg:squarederror'):
                raise ValueError()
            self._approve(metadata, 'compare_validation', m.artifact_id, validation_id, check)
            # Same explicitly approved synthetic validation rows, no labels and
            # no runtime snapshot. Original native model and independently loaded
            # Booster both receive exact float32 input in the original order.
            original = record[3].predict(matrix)
            check()
            reloaded = loaded.predict(xgb.DMatrix(matrix, feature_names=list(names), nthread=1), validate_features=True)
            check()
            for values in (original, reloaded):
                if (type(values) is not np.ndarray or values.dtype != np.float32
                        or values.shape != (len(matrix),) or not np.isfinite(values).all()
                        or np.any(np.abs(values) > 100000000)):
                    raise ValueError()
            a = original.astype('<f4', copy=False).tobytes()
            b = reloaded.astype('<f4', copy=False).tobytes()
            if a != b:
                raise ValueError()
            report = NativeRoundTripReport(artifact_id=m.artifact_id, candidate_id=metadata.candidate_id,
                validation_output_id=validation_id, validation_rows=len(matrix),
                original_prediction_sha256=_sha(a), reloaded_prediction_sha256=_sha(b))
            self._approve(metadata, 'verify_return', m.artifact_id, validation_id, check)
            if check()[0] is not record:
                raise ValueError()
            return report
        return self._operation(reference, cancelled, action)
