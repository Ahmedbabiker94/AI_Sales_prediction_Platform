"""Fixed execution allowlist. Only this adapter imports XGBoost, lazily.

Run native calls exclusively inside an explicitly isolated controlled worker.
No filenames, plugins, aliases, canonical captures, fits or writer dependencies.
"""
import hashlib
import importlib
import json
from types import MappingProxyType
import numpy as np
from src.data.contracts.model_candidate import strict_float32
from src.preprocessing.canonical_tabular import TabularOutput
from src.preprocessing.runtime_input_adapters import M3XGBoostInputAdapter


class NativeDependencyUnavailable(ValueError): pass


class XGBoostH1Adapter:
    adapter_id='owned_xgboost_3_2_0_h1_v1'

    def validate(self, evidence, request):
        output=evidence.output
        if type(output) is not TabularOutput: raise ValueError()
        output=TabularOutput.model_validate_json(output.canonical_json)
        state=evidence.state; r=output.representation; source=request.inputs.request.snapshot_request
        M3XGBoostInputAdapter().validate_context(source,state)
        if (not M3XGBoostInputAdapter().supported(state) or output.state!=state or output.model_family!='xgboost'
                or r.feature_schema!=state.feature_schema or r.compatibility_id!=evidence.metadata.representation_compatibility_id
                or r.binding.historical_evidence_digest!=request.inputs.request.snapshot_id
                or source.dataset!=state.dataset or len(output.values)>request.limits.max_rows
                or len(output.values)*len(state.feature_schema.features)>request.limits.max_cells
                or tuple(sorted(row[0].series_id for row in r.samples))!=tuple(sorted(state.dataset.series_ids))): raise ValueError()
        for row in r.samples:
            if len(row)!=1: raise ValueError()
            p=row[0]
            if (p.organization_id!=source.dataset.reference.organization_id or p.forecast_origin!=source.forecast_origin
                    or p.target_period!=source.forecast_origin or p.horizon_step!=1): raise ValueError()
        matrix=strict_float32(output.values)
        if matrix.shape!=(len(r.samples),len(state.feature_schema.features)): raise ValueError()
        matrix.flags.writeable=False
        return matrix

    def load(self, evidence):
        try: framework=importlib.import_module('xgboost')
        except (ImportError, OSError): raise NativeDependencyUnavailable() from None
        if framework.__version__!='3.2.0': raise NativeDependencyUnavailable()
        loaded=framework.Booster(params={'nthread':1})
        loaded.load_model(bytearray(evidence.native.payload))
        manifest=evidence.native.manifest
        if (tuple(loaded.feature_names or ())!=tuple(f.name for f in manifest.feature_schema.features)
                or loaded.num_features()!=len(manifest.feature_schema.features)
                or loaded.num_boosted_rounds()!=manifest.training.specification.steps
                or tuple(sorted(loaded.attributes().items()))!=manifest.native_attributes
                or json.loads(loaded.save_config())['learner']['objective']['name']!='reg:squarederror'): raise ValueError()
        return framework,loaded

    def predict(self, loaded, matrix, evidence):
        framework,booster=loaded
        names=[f.name for f in evidence.native.manifest.feature_schema.features]
        values=booster.predict(framework.DMatrix(matrix,feature_names=names,nthread=1),validate_features=True)
        if (type(values) is not np.ndarray or values.dtype!=np.float32 or values.shape!=(len(matrix),)
                or not np.isfinite(values).all() or np.any(np.abs(values)>100000000)): raise ValueError()
        raw=values.astype('<f4',copy=False).tobytes()
        return tuple(float(v) for v in values),hashlib.sha256(raw).hexdigest()


_ADAPTERS=MappingProxyType({'xgboost':XGBoostH1Adapter()})

def execution_adapter(family):
    return _ADAPTERS.get(family)
