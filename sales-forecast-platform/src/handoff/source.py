"""Protected host source inventory, not a signature or protection from host edits."""
import hashlib
import os
from pathlib import Path
import stat
from src.handoff.protocol import digest, HandoffError


def source_identity(project):
    result = {}
    for directory in ('src','tests'):
        root = project/directory
        if root.is_symlink() or not root.is_dir(): raise HandoffError('unsafe source root')
        for p in sorted(root.rglob('*')):
            if '__pycache__' in p.parts or p.suffix in ('.pyc','.pyo'):
                raise HandoffError('unreviewed cached bytecode')
            if p.is_symlink(): raise HandoffError('source symlink')
            if p.is_dir(): continue
            fd = os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|getattr(os,'O_CLOEXEC',0))
            try:
                s = os.fstat(fd)
                if not stat.S_ISREG(s.st_mode) or s.st_size > 8*1024*1024: raise HandoffError('source type/size')
                with os.fdopen(fd,'rb',closefd=False) as f: raw=f.read(8*1024*1024+1)
                if len(raw)!=s.st_size: raise HandoffError('changed source')
                result[str(p.relative_to(project))] = hashlib.sha256(raw).hexdigest()
            finally: os.close(fd)
    if not result or len(result)>4096: raise HandoffError('source inventory budget')
    return digest(result)
