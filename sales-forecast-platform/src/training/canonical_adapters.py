"""Lazy bounded native backends. No preparation, registry or artifact I/O."""
from dataclasses import dataclass
import importlib
import re
import numpy as np
from src.data.contracts.training_adapter import capability


class FrameworkUnavailable(Exception):pass


@dataclass(frozen=True)
class TrainingBatch:
    family: str
    streams: tuple[np.ndarray,...]
    targets: np.ndarray
    # Encoder mask/position metadata; empty for trees.
    missing: np.ndarray | None=None
    padding: np.ndarray | None=None
    lengths: np.ndarray | None=None
    offsets: tuple[int,...]=()


def parameter_bound(batch,spec):
    if capability(spec.family).representation=='tabular':
        # Depth fixed to 3: at most 15 nodes per tree, plus data work.
        return spec.steps*15
    width=batch.streams[0].shape[-1]*2+1;h=spec.hidden_size
    if spec.family in ('lstm','gru'):
        gates=4 if spec.family=='lstm' else 3
        return gates*(width*h+h*h+2*h)+h+1
    context=sum(a.shape[-1] for a in batch.streams[1:])
    # TransformerEncoderLayer uses dim_feedforward=2*h and one layer/head.
    return width*h+h+8*h*h+20*h+(h+context)+1


def _neural_input(batch):
    """Nonlearned explicit mask/position channels, testable without frameworks."""
    values=batch.streams[0]
    if (values.dtype not in (np.dtype('float32'),np.dtype('float64')) or values.ndim!=3
            or batch.missing.dtype!=np.dtype(bool) or batch.missing.shape!=values.shape
            or batch.padding.dtype!=np.dtype(bool) or batch.padding.shape!=values.shape[:2]
            or batch.lengths.shape!=(len(values),)
            or not np.array_equal((~batch.padding).sum(axis=1),batch.lengths)
            or len(batch.offsets)!=values.shape[1]
            or any(type(v) is not int or not -4096<=v<0 for v in batch.offsets)):
        raise ValueError('exact mask/position geometry required')
    observed=np.where(batch.missing,0,values)
    offsets=np.broadcast_to(np.array(batch.offsets,dtype=values.dtype)[None,:,None],(*values.shape[:2],1))
    return np.concatenate((observed,batch.missing.astype(values.dtype),offsets),axis=-1)


class NativeBackend:
    kind='native_framework'

    def __init__(self,family):
        capability(family)
        self.family=family
        module='xgboost' if family=='xgboost' else 'lightgbm' if family=='lightgbm' else 'torch'
        try:self.framework=importlib.import_module(module)
        except (ImportError,ModuleNotFoundError,OSError):raise FrameworkUnavailable() from None
        self.version=str(getattr(self.framework,'__version__',''))
        match=re.match(r'^(\d+)\.',self.version)
        supported={'xgboost':(2,3),'lightgbm':(4,),'torch':(2,)}
        if match is None or len(self.version)>128 or int(match[1]) not in supported[module]:raise FrameworkUnavailable()

    def fit(self,batch,spec,checkpoint):
        checkpoint()
        if self.family in ('xgboost','lightgbm'):
            cls=self.framework.XGBRegressor if self.family=='xgboost' else self.framework.LGBMRegressor
            params=dict(n_estimators=spec.steps,max_depth=3,learning_rate=spec.learning_rate,random_state=spec.seed,n_jobs=1)
            if self.family=='xgboost':params.update(objective='reg:squarederror',tree_method='hist',device='cpu')
            else:params.update(verbosity=-1,deterministic=True,force_col_wise=True,num_leaves=8)
            model=cls(**params);checkpoint()
            model.fit(batch.streams[0],batch.targets.reshape(-1))
            checkpoint();return model
        return self._neural(batch,spec,checkpoint)

    def _neural(self,batch,spec,checkpoint):
        torch=self.framework;nn=torch.nn
        dtype=torch.float32 if spec.input_dtype=='float32' else torch.float64
        # Zero only declared unavailable cells and expose their masks and offsets
        # as exact discrete channels. Never cast accepted feature values silently.
        augmented=_neural_input(batch)
        x=torch.tensor(augmented,dtype=dtype,device='cpu');y=torch.tensor(batch.targets,dtype=dtype,device='cpu')
        pads=torch.tensor(batch.padding,dtype=torch.bool,device='cpu')
        hidden=spec.hidden_size; width=augmented.shape[-1]
        context_size=sum(a.shape[-1] for a in batch.streams[1:])
        context=(torch.cat([torch.tensor(a.reshape(len(y),-1).copy(),dtype=dtype,device='cpu') for a in batch.streams[1:]],dim=1)
                 if spec.family=='transformer' else None)
        class Recurrent(nn.Module):
            def __init__(self):
                super().__init__()
                cls=nn.LSTM if spec.family=='lstm' else nn.GRU
                self.rnn=cls(width,hidden,batch_first=True,dtype=dtype,device='cpu')
                self.head=nn.Linear(hidden,1,dtype=dtype,device='cpu')
            def forward(self,values,padding,context=None):
                # Move only nonpadding positions into pack order; preserve temporal
                # order and interior missing tokens. Lengths were validated by M2.
                sequences=[row[~pad] for row,pad in zip(values,padding)]
                packed=nn.utils.rnn.pack_sequence(sequences,enforce_sorted=False)
                _,state=self.rnn(packed)
                if spec.family=='lstm':state=state[0]
                return self.head(state[-1])
        class Transformer(nn.Module):
            def __init__(self):
                super().__init__()
                self.project=nn.Linear(width,hidden,dtype=dtype,device='cpu')
                layer=nn.TransformerEncoderLayer(hidden,1,dim_feedforward=2*hidden,dropout=0.,batch_first=True,dtype=dtype,device='cpu')
                self.encoder=nn.TransformerEncoder(layer,1,enable_nested_tensor=False)
                self.head=nn.Linear(hidden+context_size,1,dtype=dtype,device='cpu')
            def forward(self,values,padding,context=None):
                z=self.encoder(self.project(values),src_key_padding_mask=padding)
                z=z.masked_fill(padding[:,:,None],0.).sum(dim=1)/(~padding).sum(dim=1)[:,None]
                return self.head(torch.cat((z,context),dim=1))
        # Process-local orchestration serializes calls. This restores CPU RNG state;
        # it is not isolation from unrelated host threads or framework version drift.
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(spec.seed)
            model=Transformer() if spec.family=='transformer' else Recurrent()
            if sum(p.numel() for p in model.parameters())>spec.resources.max_parameters:raise ValueError()
            optimizer=torch.optim.SGD(model.parameters(),lr=spec.learning_rate)
            for _ in range(spec.steps):
                checkpoint();optimizer.zero_grad();loss=nn.functional.mse_loss(model(x,pads,context),y)
                if not bool(torch.isfinite(loss)):raise ValueError()
                loss.backward();optimizer.step();checkpoint()
            if any(not bool(torch.isfinite(p).all()) for p in model.parameters()):raise ValueError()
            model.eval();checkpoint();return model


class FamilyTrainingAdapter:
    """Explicit capability selection, never a fallback to a different family."""
    def __init__(self,family):self.capability=capability(family)

    def fit(self,batch,spec,backend_factory,checkpoint):
        if batch.family!=self.capability.family or spec.family!=self.capability.family:raise ValueError()
        limit=spec.resources.max_tree_nodes if self.capability.representation=='tabular' else spec.resources.max_parameters
        if parameter_bound(batch,spec)>limit:raise ValueError()
        checkpoint();backend=backend_factory(spec.family)
        if backend.family!=spec.family or backend.kind not in ('native_framework','test_stub'):raise ValueError()
        checkpoint();model=backend.fit(batch,spec,checkpoint);checkpoint()
        if model is None:raise ValueError()
        return model,backend.kind,backend.version
