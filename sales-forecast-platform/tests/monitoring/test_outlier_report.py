"""Bounded opt-in report publication, isolated under pytest temporary paths."""
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from src.monitoring import outlier_analysis as report


def inputs():
    frame = pd.DataFrame({'Store': [1] * 60, 'Dept': [2] * 60,
                          'Date': pd.date_range('2025-01-01', periods=60),
                          'private': ['NEVER_EXPORT' * 10000] * 60})
    return pd.Series(np.arange(60, dtype=float)), np.zeros(60), frame


def export(path, **kwargs):
    return report.export_outlier_report(*inputs(), report.OutlierReportOptions(str(path), **kwargs))


def test_explicit_export_allowlist_limits_and_permissions(tmp_path):
    path = tmp_path / 'report.csv'
    result = export(path)
    assert result['status'] == 'WRITTEN' and result['rows'] == 50
    payload = path.read_bytes()
    assert len(payload) == result['bytes'] <= report.MAX_REPORT_BYTES
    assert tuple(pd.read_csv(path).columns) == report.REPORT_COLUMNS
    assert b'NEVER_EXPORT' not in payload and b'private' not in payload
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob('.outlier-*.tmp'))


def test_byte_cap_checked_before_any_filesystem_open(tmp_path, monkeypatch):
    forbidden = Mock(side_effect=AssertionError('filesystem open before serialization admission'))
    monkeypatch.setattr(report.os, 'open', forbidden)
    with pytest.raises(ValueError, match='byte limit'):
        export(tmp_path / 'report.csv', max_bytes=1)
    forbidden.assert_not_called()


def test_exact_serialized_byte_boundary(tmp_path):
    payload, _ = report._report_bytes(*inputs(), report.OutlierReportOptions('unused'))
    assert export(tmp_path / 'exact.csv', max_bytes=len(payload))['bytes'] == len(payload)
    with pytest.raises(ValueError, match='byte limit'):
        export(tmp_path / 'short.csv', max_bytes=len(payload) - 1)
    assert not (tmp_path / 'short.csv').exists()


def test_utf8_byte_cap_is_not_a_character_cap(tmp_path, monkeypatch):
    actual, predicted, frame = inputs()
    frame['Date'] = '\u00e9' * 1000
    forbidden = Mock(side_effect=AssertionError('write before UTF-8 admission'))
    monkeypatch.setattr(report.os, 'open', forbidden)
    with pytest.raises(ValueError, match='byte limit'):
        report.export_outlier_report(actual, predicted, frame,
                                     report.OutlierReportOptions(str(tmp_path / 'report.csv')))
    forbidden.assert_not_called()


def test_implicit_overwrite_rejects_explicit_overwrite_succeeds(tmp_path):
    path = tmp_path / 'report.csv'
    path.write_bytes(b'original')
    with pytest.raises(ValueError):
        export(path)
    assert path.read_bytes() == b'original'
    export(path, overwrite=True)
    assert path.read_bytes().startswith(b'Store,Dept,Date,')
    assert not list(tmp_path.glob('.outlier-*.tmp'))


@pytest.mark.parametrize('kind', ['target_symlink', 'parent_symlink', 'traversal', 'missing_parent', 'directory', 'non_csv'])
def test_unsafe_paths_reject_without_modifying_existing_content(tmp_path, kind):
    actual = tmp_path / 'actual'
    actual.mkdir()
    protected = actual / 'protected.csv'
    protected.write_bytes(b'original')
    if kind == 'target_symlink':
        path = tmp_path / 'link.csv'
        path.symlink_to(protected)
    elif kind == 'parent_symlink':
        parent = tmp_path / 'parent'
        parent.symlink_to(actual, target_is_directory=True)
        path = parent / 'protected.csv'
    elif kind == 'traversal':
        path = actual / '..' / 'report.csv'
    elif kind == 'missing_parent':
        path = tmp_path / 'missing' / 'report.csv'
    elif kind == 'non_csv':
        path = tmp_path / 'current.json'
        path.write_bytes(b'original')
    else:
        path = tmp_path / 'directory.csv'
        path.mkdir()
    with pytest.raises((ValueError, OSError)):
        export(path, overwrite=True)
    assert protected.read_bytes() == b'original'
    if kind == 'non_csv':
        assert path.read_bytes() == b'original'
    assert not (tmp_path / 'missing').exists()
    assert not list(tmp_path.rglob('.outlier-*.tmp'))


@pytest.mark.parametrize('phase', ['write', 'short_write', 'flush', 'publish'])
def test_failed_write_or_publication_preserves_destination_and_cleans_up(tmp_path, monkeypatch, phase):
    path = tmp_path / 'report.csv'
    path.write_bytes(b'original')
    if phase == 'flush':
        monkeypatch.setattr(report.os, 'fsync', Mock(side_effect=OSError('failure')))
    elif phase == 'publish':
        monkeypatch.setattr(report.os, 'replace', Mock(side_effect=OSError('failure')))
    else:
        original_fdopen = report.os.fdopen
        class FailedWrite:
            def __init__(self, fd): self.stream = original_fdopen(fd, 'wb')
            def __enter__(self): return self
            def write(self, payload):
                self.stream.write(payload[:10])
                if phase == 'short_write':
                    return 10
                raise OSError('partial temporary write')
            def __exit__(self, *args): self.stream.close()
        monkeypatch.setattr(report.os, 'fdopen', lambda fd, mode: FailedWrite(fd))
    with pytest.raises(OSError):
        export(path, overwrite=True)
    assert path.read_bytes() == b'original'
    assert not list(tmp_path.glob('.outlier-*.tmp'))


def test_create_only_race_does_not_replace_destination(tmp_path, monkeypatch):
    path = tmp_path / 'report.csv'
    link = report.os.link
    def raced_link(*a, **kwargs):
        path.write_bytes(b'racing writer')
        return link(*a, **kwargs)
    monkeypatch.setattr(report.os, 'link', raced_link)
    with pytest.raises(FileExistsError):
        export(path)
    assert path.read_bytes() == b'racing writer'
    assert not list(tmp_path.glob('.outlier-*.tmp'))


def test_temporary_collision_never_deletes_foreign_file(tmp_path, monkeypatch):
    monkeypatch.setattr(report.secrets, 'token_hex', lambda _: 'collision')
    foreign = tmp_path / '.outlier-collision.tmp'
    foreign.write_bytes(b'foreign file')
    with pytest.raises(FileExistsError):
        export(tmp_path / 'report.csv')
    assert foreign.read_bytes() == b'foreign file'


@pytest.mark.parametrize('option,value', [('overwrite', 1), ('max_bytes', True), ('max_bytes', 65537),
                                        ('max_bytes', 0), ('top_n', True), ('top_n', 51)])
def test_invalid_options_reject_without_files(tmp_path, option, value):
    with pytest.raises(ValueError):
        export(tmp_path / 'report.csv', **{option: value})
    assert list(tmp_path.iterdir()) == []
