"""Local candidate-only immutable blobs, Linux local-filesystem scope.

The host must supply an owner-only dedicated directory outside production,
network and synchronized storage. Dir-fd/no-follow operations prevent path
substitution; read-only modes protect against accidents, not the same OS user.
Atomic hard-link publication refuses even a concurrently created destination.
"""
import hashlib
from itertools import islice
import os
import re
import stat
import struct
from uuid import uuid4
from src.data.contracts.candidate_package import CandidatePackageManifest

MAGIC = b'M7CAND1\n'
MAX_PACKAGE_BYTES = 1024*1024


class PackageStoreError(ValueError):
    pass


def reject_store():
    error = PackageStoreError('candidate package operation rejected')
    try:
        raise error from None
    finally:
        error.__cause__ = None
        error.__context__ = None


def package_key(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        reject_store()
    return value


def _encode_package(manifest, payload):
    if type(manifest) is not CandidatePackageManifest or type(payload) is not bytes:
        raise ValueError()
    manifest = CandidatePackageManifest.model_validate_json(manifest.canonical_json)
    if len(payload) != manifest.payload_size or hashlib.sha256(payload).hexdigest() != manifest.payload_sha256:
        raise ValueError()
    encoded = manifest.canonical_bytes
    result = MAGIC + struct.pack('>I', len(encoded)) + encoded + payload
    if len(result) > MAX_PACKAGE_BYTES:
        raise ValueError()
    return result


def _decode_package(data, expected_id):
    package_key(expected_id)
    if type(data) is not bytes or len(data) > MAX_PACKAGE_BYTES or not data.startswith(MAGIC):
        raise ValueError()
    start = len(MAGIC)+4
    size = struct.unpack('>I', data[len(MAGIC):start])[0]
    if not 0 < size <= MAX_PACKAGE_BYTES-start or start+size >= len(data):
        raise ValueError()
    raw = data[start:start+size]
    manifest = CandidatePackageManifest.model_validate_json(raw)
    if raw != manifest.canonical_bytes or manifest.package_id != expected_id:
        raise ValueError()
    payload = data[start+size:]
    if len(payload) != manifest.payload_size or hashlib.sha256(payload).hexdigest() != manifest.payload_sha256:
        raise ValueError()
    # This format has no model weights. Its entire vocabulary is fixed.
    from src.data.contracts.dataset import operational_json
    expected = operational_json({'version': '1.0', 'classification': 'TEST_ONLY_NON_DEPLOYABLE',
        'candidate_id': manifest.candidate_id, 'family': manifest.training.specification.family,
        'content': 'NO_MODEL_PAYLOAD'}).encode('utf-8')
    if payload != expected:
        raise ValueError()
    return manifest


def validate_publication(manifest, payload):
    """Pure, guarded request validation shared by first and repeated writes."""
    try:
        data = _encode_package(manifest, payload)
        canonical = _decode_package(data, manifest.package_id)
        return canonical, data
    except Exception:
        pass
    reject_store()


class CandidatePackageRepository:
    def __init__(self, root):
        self._fd = None
        try:
            if type(root) is not str or not root.startswith('/') or '\x00' in root:
                raise ValueError()
            parts = root.split('/')[1:]
            if not parts or any(not p or p in ('.', '..') for p in parts):
                raise ValueError()
            # Never follow any component, including a symlinked ancestor.
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            try:
                for part in parts:
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd); fd = nxt
                info = os.fstat(fd)
                if info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise ValueError()
                # Deliberate dedicated namespace; never use a production pointer.
                try:
                    os.mkdir('candidate-packages-v1', 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                self._fd = os.open('candidate-packages-v1', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                info = os.fstat(self._fd)
                if info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise ValueError()
            finally:
                os.close(fd)
        except Exception:
            self.close()
        else:
            return
        reject_store()

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def publish(self, manifest, payload):
        staged = None
        linked = False
        key = None
        try:
            if self._fd is None:
                raise ValueError()
            with os.scandir(self._fd) as entries:
                if sum(1 for _ in islice(entries, 64)) >= 64:
                    raise ValueError()
            manifest, data = validate_publication(manifest, payload)
            key = package_key(manifest.package_id)+'.candidate'
            staged = '.stage-'+uuid4().hex
            fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._fd)
            try:
                try:
                    remaining = memoryview(data)
                    while remaining:
                        written = os.write(fd, remaining)
                        if written <= 0:
                            raise OSError()
                        remaining = remaining[written:]
                    os.fchmod(fd, 0o400); os.fsync(fd)
                finally:
                    os.close(fd)
                # link is create-only even when an empty destination/symlink races us.
                os.link(staged, key, src_dir_fd=self._fd, dst_dir_fd=self._fd, follow_symlinks=False)
                linked = True
                os.fsync(self._fd)
            finally:
                os.unlink(staged, dir_fd=self._fd); staged = None
            return
        except Exception:
            # A durability failure after link must not leave a reported-failed
            # package published. The store is private to this trusted operator.
            if linked:
                try:
                    os.unlink(key, dir_fd=self._fd)
                except OSError:
                    pass  # storage failure can defeat cleanup; never report success
        reject_store()

    def verify(self, package_id):
        try:
            if self._fd is None:
                raise ValueError()
            key = package_key(package_id)+'.candidate'
            fd = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._fd)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_PACKAGE_BYTES
                        or info.st_uid != os.getuid() or info.st_mode & 0o222):
                    raise ValueError()
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    data = stream.read(MAX_PACKAGE_BYTES+1)
                after = os.fstat(fd)
                if any(getattr(after, k) != getattr(info, k) for k in ('st_ino', 'st_dev', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
                    raise ValueError()
                return _decode_package(data, package_id)
            finally:
                os.close(fd)
        except Exception:
            pass
        reject_store()
