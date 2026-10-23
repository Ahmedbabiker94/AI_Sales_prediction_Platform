"""Opt-in local CSV reporting, not model registration or production publication.

Only Store, Dept, Date and calculated actual/predicted/residual/absolute_error/
percentage_error are exported. At most 50 rows and 64 KiB UTF-8 are permitted.
No directories are created; callers select a .csv file in an existing parent and explicitly
choose overwrite. POSIX directory-relative operations reject symlink components.
This is local filesystem safety, not a sandbox against a hostile host/operator.
"""
from dataclasses import dataclass
import io
import os
from pathlib import Path
import secrets
import stat

import numpy as np
import pandas as pd


MAX_REPORT_BYTES = 64 * 1024
REPORT_COLUMNS = ('Store', 'Dept', 'Date', 'actual', 'predicted', 'residual',
                  'absolute_error', 'percentage_error')


@dataclass(frozen=True)
class OutlierReportOptions:
    output_path: str
    overwrite: bool = False
    max_bytes: int = MAX_REPORT_BYTES
    top_n: int = 50


class _BoundedCSV(io.StringIO):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit
        self.size = 0

    def write(self, text):
        size = len(text.encode('utf-8'))
        if self.size + size > self.limit:
            raise ValueError('report byte limit exceeded')
        self.size += size
        return super().write(text)


def _report_bytes(y_true, y_pred, original_df, options):
    # Select before copying; unrelated input columns never enter the report.
    frame = original_df.loc[:, ['Store', 'Dept', 'Date']].copy().reset_index(drop=True)
    actual = np.asarray(y_true, dtype=float)
    predicted = np.asarray(y_pred, dtype=float)
    if actual.shape != (len(frame),) or predicted.shape != actual.shape:
        raise ValueError('invalid report shape')
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ValueError('invalid report numbers')
    with np.errstate(over='raise', invalid='raise', divide='raise'):
        residual = actual - predicted
        absolute = np.abs(residual)
        percentage = np.zeros(len(frame), dtype=float)
        np.divide(absolute, np.abs(actual), out=percentage, where=actual != 0)
        percentage *= 100
    frame['actual'] = actual
    frame['predicted'] = predicted
    frame['residual'] = residual
    frame['absolute_error'] = absolute
    frame['percentage_error'] = percentage
    frame = frame.sort_values('absolute_error', ascending=False, kind='stable').head(options.top_n)
    buffer = _BoundedCSV(options.max_bytes)
    frame.loc[:, list(REPORT_COLUMNS)].to_csv(buffer, index=False, lineterminator='\n')
    return buffer.getvalue().encode('utf-8'), len(frame)


def _open_parent(path):
    # Pin every directory by fd, rejecting symlink traversal and '..'. No mkdir.
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parent.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _publish(payload, path, overwrite):
    parent_fd = _open_parent(path)
    temporary = None
    try:
        try:
            existing = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not stat.S_ISREG(existing.st_mode) or not overwrite:
                raise ValueError('report destination unavailable')
        candidate = '.outlier-' + secrets.token_hex(16) + '.tmp'
        fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent_fd)
        temporary = candidate  # Only clean up a file this call actually created.
        try:
            stream = os.fdopen(fd, 'wb')
        except BaseException:
            os.close(fd)
            raise
        with stream:
            if stream.write(payload) != len(payload):
                raise OSError('incomplete report write')
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            temporary = None
        else:
            # Atomic create-only publication; a racing destination cannot be overwritten.
            os.link(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                    follow_symlinks=False)
            os.unlink(temporary, dir_fd=parent_fd)
            temporary = None
    finally:
        try:
            if temporary is not None:
                os.unlink(temporary, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)


def export_outlier_report(y_true, y_pred, original_df, options):
    """Explicit export; raises on failure. Training converts failures to status.

    The entire bounded CSV is serialized before any filesystem write. Publication
    uses an adjacent private temporary file and atomic replace/create-only link.
    A cleanup failure is reported, never hidden. A completed file may exist if
    cleanup fails after atomic publication; no partial destination is published.
    Memory/CPU and crash durability are not hard-enforced. POSIX only.
    """
    if type(options) is not OutlierReportOptions:
        raise ValueError('invalid report options')
    if (type(options.output_path) is not str or not options.output_path
            or '\x00' in options.output_path or type(options.overwrite) is not bool
            or type(options.max_bytes) is not int or not 1 <= options.max_bytes <= MAX_REPORT_BYTES
            or type(options.top_n) is not int or not 1 <= options.top_n <= 50):
        raise ValueError('invalid report options')
    requested = Path(options.output_path)
    if ('..' in requested.parts or not requested.name or options.output_path.endswith('/')
            or requested.suffix.lower() != '.csv'):
        raise ValueError('invalid report path')
    path = Path(os.path.abspath(requested))
    payload, rows = _report_bytes(y_true, y_pred, original_df, options)
    _publish(payload, path, options.overwrite)
    return {'status': 'WRITTEN', 'report_path': str(path), 'rows': rows, 'bytes': len(payload)}
