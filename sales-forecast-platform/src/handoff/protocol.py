"""Bounded data framing, NOT authentication or execution/write authority.

Only the protected host-created Docker attach channel authenticates invocation.
Public encoded frames, nonces, digests and declarations never authenticate it.
"""
import hashlib
import json
import math
import os
import re
import selectors
import struct
import time

VERSION = 'r7_native_handoff_v1'
PURPOSE = 'CONTROLLED_OFFLINE_H1_VALIDATED_NO_WRITE'
MAX_FRAME = 1152 * 1024
MAX_TOTAL = 4 * 1024 * 1024
KINDS = {
    'HELLO': ('host', 0, {'idempotency', 'deadline'}),
    'PROPOSAL': ('worker', 1, {'records', 'projection'}),
    'APPROVAL': ('host', 2, {'proposal', 'approval'}),
    'RESERVED': ('worker', 3, {'proposal', 'approval', 'grant', 'reservation'}),
    'CHALLENGE': ('host', 4, {'proposal', 'approval', 'reservation', 'final_challenge'}),
    'FINAL_RELEASE': ('worker', 5, {'proposal', 'approval', 'reservation', 'final_challenge', 'grant', 'release', 'expires'}),
    'ACK': ('host', 6, {'release', 'state'}),
}


class HandoffError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def hex64(value):
    if type(value) is not str or re.fullmatch('[a-f0-9]{64}', value) is None:
        raise HandoffError('invalid identity')
    return value


def decode(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result: raise HandoffError('duplicate field')
            result[k] = v
        return result
    def bad(_): raise HandoffError('invalid number')
    try:
        text = raw.decode('utf-8', errors='strict')
        if text.startswith('\ufeff'): raise HandoffError('invalid encoding')
        return json.loads(text, object_pairs_hook=pairs, parse_constant=bad)
    except (ValueError, UnicodeError, RecursionError) as e:
        raise HandoffError('invalid encoding') from None


def frame(kind, context, body):
    direction, sequence, fields = KINDS[kind]
    value = dict(version=VERSION, purpose=PURPOSE, kind=kind, direction=direction,
                 sequence=sequence, **context, body=body)
    validate(value, kind, context)
    raw = canonical(value).encode()
    if not 0 < len(raw) <= MAX_FRAME: raise HandoffError('frame budget')
    return struct.pack('>I', len(raw)) + raw


def validate(value, kind, context):
    direction, sequence, fields = KINDS[kind]
    if (type(value) is not dict or set(value) != {'version','purpose','kind','direction','sequence','session','container','source','challenge','body'}
            or value['version'] != VERSION or value['purpose'] != PURPOSE or value['kind'] != kind
            or value['direction'] != direction or type(value['sequence']) is not int or value['sequence'] != sequence
            or any(value[k] != v for k, v in context.items())
            or type(value['body']) is not dict or set(value['body']) != fields):
        raise HandoffError('foreign or out-of-order message')
    for key in ('session','container','source','challenge'): hex64(value[key])
    b = value['body']
    for key in fields - {'deadline','records','projection','expires','state'}: hex64(b[key])
    for key in fields & {'deadline','expires'}:
        if type(b[key]) not in (float,int) or not math.isfinite(b[key]): raise HandoffError('invalid deadline')
    if 'state' in b and b['state'] != 'VALIDATED_NO_WRITE': raise HandoffError('invalid acknowledgement')
    for key in fields & {'records','projection'}:
        if type(b[key]) is not str: raise HandoffError('canonical content required')
    return b


class Channel:
    """Single-owner, bounded duplex FDs. stderr drained concurrently if supplied.

    All writes and reads share one monotonic deadline; no unbounded buffering.
    Diagnostic flood fails closed. Callers own the FDs/process lifecycle.
    """
    def __init__(self, reader, writer, *, deadline, stderr=None, cancelled=lambda: False):
        if not 0 < deadline-time.monotonic() <= 240: raise HandoffError('channel deadline')
        self.reader, self.writer, self.stderr = reader, writer, stderr
        self.deadline, self.cancelled = deadline, cancelled
        self.sent = self.received = 0
        self.diagnostics = bytearray()
        for fd in (reader,writer,stderr):
            if fd is not None: os.set_blocking(fd, False)

    def _ready(self, fd, events):
        while True:
            if self.cancelled() is not False or time.monotonic() >= self.deadline:
                raise HandoffError('cancelled or deadline')
            with selectors.DefaultSelector() as s:
                s.register(fd, events)
                if self.stderr is not None: s.register(self.stderr, selectors.EVENT_READ)
                ready = s.select(min(.1, max(0, self.deadline-time.monotonic())))
            target = False
            for key, _ in ready:
                if key.fd == self.stderr:
                    data = os.read(self.stderr, 8192)
                    if not data: self.stderr = None
                    else:
                        self.diagnostics.extend(data)
                        if len(self.diagnostics) > 256*1024: raise HandoffError('diagnostic budget')
                else: target = True
            if target: return

    def send(self, raw):
        if type(raw) is not bytes or not 4 < len(raw) <= MAX_FRAME+4 or self.sent+len(raw) > MAX_TOTAL:
            raise HandoffError('send budget')
        self.sent += len(raw)
        view = memoryview(raw)
        while view:
            self._ready(self.writer, selectors.EVENT_WRITE)
            n = os.write(self.writer, view[:16384])
            view = view[n:]

    def _read(self, n):
        data = bytearray()
        while len(data) < n:
            self._ready(self.reader, selectors.EVENT_READ)
            chunk = os.read(self.reader, min(16384, n-len(data)))
            if not chunk: raise HandoffError('truncated or crashed worker')
            data.extend(chunk)
        return bytes(data)

    def receive(self, kind, context):
        n, = struct.unpack('>I', self._read(4))
        if not 0 < n <= MAX_FRAME or self.received+n+4 > MAX_TOTAL: raise HandoffError('receive budget')
        self.received += n+4
        value = decode(self._read(n))
        validate(value, kind, context)
        return value['body']
