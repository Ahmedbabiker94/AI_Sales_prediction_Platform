"""Host-private CPython metadata order. No SQL, mutex, callbacks or I/O.

Exact builtin dict.setdefault with an exact integer key is the single-assignment
linearization. The cursor is only a hint; the immutable prefix is authoritative.
Unsupported runtimes deny. This module is not a public authority constructor.
"""
from dataclasses import dataclass,field
import hashlib
from sys import implementation,version_info
_SUPPORTED=implementation.name=='cpython' and version_info[:2] in ((3,11),(3,12))
_MAX_RECORDS=65536
class OrderDenied(ValueError):pass
@dataclass(frozen=True,eq=False)
class _Event:
    kind:str
    identity:object
@dataclass(frozen=True,eq=False)
class _Node:
    index:int
    event:_Event
    next:dict=field(default_factory=dict,repr=False)
class _Order:
    def __init__(self):
        self._root=_Node(0,_Event('ROOT',None))
        self._cursor=self._root
        self._intent={}
        self._terminal=_Event('Q',None)
    def _end(self):
        node=self._cursor
        while node.event.kind!='Q':
            child=node.next.get(0)
            if child is None:break
            node=child
        self._cursor=node
        return node
    def terminal(self):
        if self._intent:self._append(self._terminal)
        return self._end().event.kind=='Q'
    def quarantine(self):
        # Persistent intent precedes append; another request helps finish it if
        # a signal interrupts this caller. Never cleared or replaced by a token.
        self._intent.setdefault(0,self._terminal)
        return self._append(self._terminal)
    def commit(self,kind,identity):
        if type(kind) is not str or kind not in ('I','S','P_READ','P_EVAL'):
            raise OrderDenied('original metadata operation required')
        event=_Event(kind,identity)
        node=self._append(event)
        if node.event is not event:raise OrderDenied('host shutdown required')
        return node
    def _append(self,event):
        if not _SUPPORTED:raise OrderDenied('unsupported host runtime')
        node=self._end()
        while node.event.kind!='Q':
            if self._intent or node.index>=_MAX_RECORDS-1:
                self._intent.setdefault(0,self._terminal)
                event=self._terminal
            proposal=_Node(node.index+1,event)
            # Both proposals/cleanup-independent records exist before this C
            # operation. No Python hash/equality callback (key is exact int0).
            chosen=node.next.setdefault(0,proposal)
            self._cursor=chosen
            if chosen is proposal:return chosen
            node=chosen
        return node
    def snapshot(self):
        # Private diagnostic snapshot only, never a reconstruction capability.
        rows=[];node=self._root
        while True:
            node=node.next.get(0)
            if node is None:break
            rows.append((node.index,node.event.kind,node.event.identity))
        return tuple(rows)

# Original host singletons; consumers never choose an order, claim or owner.
_ORDER=_Order()
from dis import Bytecode,get_instructions
from sys import _getframe
from threading import get_ident
from time import monotonic
from weakref import WeakValueDictionary
_SEAL=object()
_CLAIMS=WeakValueDictionary()
_ISSUED={}
_BOOTSTRAP_CREDENTIAL=object()
_ORIGINS={}
class _Origin:
    __slots__=()
    def __reduce__(self):raise TypeError('original host origin only')
@dataclass(frozen=True,eq=False)
class _OriginRecord:
    handle:object
    kind:str
    provider:object
    source:object
    peer:object
    owner_code:object

def _register_origin(credential,kind,provider,source,peer,owner_code):
    if credential is not _BOOTSTRAP_CREDENTIAL or kind not in ('READ','EVAL'):
        raise OrderDenied('protected host bootstrap required')
    handle=_Origin()
    _ORIGINS[id(handle)]=_OriginRecord(handle,kind,provider,source,peer,owner_code)
    return handle

class _Claim:
    __slots__=()
    def __reduce__(self):raise TypeError('original claim only')
@dataclass(eq=False)
class _ClaimRecord:
    handle:object
    owner:object
    provider:object
    scope_json:str
    selection_json:str
    idempotency:str
    digest:str
    authorization:object
    deadline:float
    use:dict=field(default_factory=dict)
    start:object=None
    finished:bool=False

@dataclass(frozen=True,eq=False)
class _Published:
    origin:object
    provider:object
    operation:object
    grants:tuple
    selection_json:str
    kind:str
    result_sha256:str

class _Operation:
    """Owning context entered before S/P. Frame metadata is not GC ownership."""
    def __init__(self,seal,origin,selection,grant,peer_grant):
        if seal is not _SEAL:raise OrderDenied('original host operation only')
        self.origin=origin
        self.kind,self.provider,self.selection,self.grant=origin.kind,origin.provider,selection,grant
        self.peer_grant=peer_grant
        self.owner_code=origin.owner_code
        self.handle=object();self.entered=self.closed=False
        self.frame_id=self.thread=None;self.offsets=frozenset()
        self.claim=None;self.publication_use={};self.result=None;self.publication=None
    def __reduce__(self):raise TypeError('original host operation only')
    def __enter__(self):
        if not _SUPPORTED or self.entered or self.closed:raise OrderDenied('original operation unavailable')
        frame=_getframe(1);instructions=tuple(get_instructions(frame.f_code,show_caches=True))
        entry=next((i for i in instructions if i.offset==frame.f_lasti),None)
        if frame.f_code is not self.owner_code or entry is None or entry.opname!='BEFORE_WITH':raise OrderDenied('metadata unavailable')
        region=next((e for e in Bytecode(frame.f_code).exception_entries if e.start==entry.offset+2),None)
        if region is None:raise OrderDenied('metadata unavailable')
        # Only fixed reviewed service code can own this scope. Compiler exception
        # ranges, not optional debug positions, delimit its simple with-body.
        self.offsets=frozenset(i.offset for i in instructions if any(e.target==region.target and e.start<=i.offset<e.end for e in Bytecode(frame.f_code).exception_entries))
        self.frame_id,self.thread=id(frame),get_ident();self.entered=True
        return self
    def live(self):
        if not self.entered or self.closed or self.thread!=get_ident():return False
        frame=_getframe(1)
        while frame is not None:
            if id(frame)==self.frame_id and frame.f_lasti in self.offsets and any(v is self for v in frame.f_locals.values()):return True
            frame=frame.f_back
        return False
    def __exit__(self,*exc):
        self.closed=True
        if self.claim is not None:_CLAIMS.pop(id(self.claim.handle),None)
        return False
    def admit(self,authorization,remaining):
        if self.kind!='READ' or not self.live() or self.claim is not None or not 0<remaining<=60:raise OrderDenied('original claim required')
        h=_Claim();selection=self.selection
        record=_ClaimRecord(h,self,self.provider,selection.scope.canonical_json,selection.canonical_json,selection.idempotency_id,selection.expected_content_sha256,authorization,monotonic()+remaining)
        # Scope owns record before registration and irreversible S. No returned
        # local-variable assignment is required to keep this ownership active.
        self.claim=record;_CLAIMS[id(h)]=record
        record.start=_ORDER.commit('S',h)
        return h
    def publish(self,result):
        if not self.live():raise OrderDenied('original publication required')
        if self.kind=='READ':
            from src.data.contracts.canonical_forecast_read import CanonicalReadOutcome
            if type(result) is not CanonicalReadOutcome or result.status!='VERIFIED_RECORD_SET' or self.claim is None or not self.claim.finished:raise OrderDenied('unverified record set')
            batch=result.records.stored.batch
            if batch.scope.canonical_json!=self.claim.scope_json or batch.idempotency_id!=self.claim.idempotency or batch.content_sha256!=self.claim.digest:raise OrderDenied('unapproved content')
            kind='P_READ'
        else:
            from src.data.contracts.stored_forecast_accuracy import AccuracyOutcome
            if self.kind!='EVAL' or type(result) is not AccuracyOutcome or result.status!='VERIFIED_EVALUATION':raise OrderDenied('unverified evaluation')
            kind='P_EVAL'
        # Preparation and canonical validation precede the atomic commit.
        self.result=result
        marker=object()
        if self.publication_use.setdefault(0,marker) is not marker:raise OrderDenied('publication already attempted')
        original = _Published(self.origin.handle,self.provider,self.handle,(self.grant,self.peer_grant),self.selection.canonical_json,kind,hashlib.sha256(result.canonical_json.encode()).hexdigest())
        self.publication=_ORDER.commit(kind,original)
        return result

def _operation(origin,selection,grant,peer_grant=None):
    record=_ORIGINS.get(id(origin))
    if type(origin) is not _Origin or record is None or record.handle is not origin:
        raise OrderDenied('original host origin required')
    return _Operation(_SEAL,record,selection,grant,peer_grant)
def _consume_claim(provider,handle,idempotency):
    record=_CLAIMS.get(id(handle))
    if (type(handle) is not _Claim or record is None or record.handle is not handle
        or type(record.owner) is not _Operation or not record.owner.live()
        or record.provider is not provider or record.idempotency!=idempotency
        or record.scope_json!=provider.repository.scope.canonical_json
        or record.start is None or record.start.event.kind!='S' or record.start.event.identity is not handle
        or not monotonic()<record.deadline):raise OrderDenied('original claim required')
    marker=object()
    if record.use.setdefault(0,marker) is not marker:raise OrderDenied('claim consumed')
    # S was committed before Q, so A1 explicitly allows this one consumption
    # after Q. No fresh health check can turn it into additional permission.
    return record


def _activate_original(handle):
    if id(handle) in _ISSUED:raise OrderDenied('already issued')
    node=_ORDER.commit('I',handle)
    _ISSUED[id(handle)]=(handle,node)
    return handle

def _issued_original(handle):
    record=_ISSUED.get(id(handle))
    return record is not None and record[0] is handle and record[1].event.kind=='I' and record[1].event.identity is handle
