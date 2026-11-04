"""Offline lifecycle inventory CLI (docs/done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md §2.6, §4)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from personality import lifecycle as L
from tests.test_personality_lifecycle import START, classes, snapshot, universe
from tools import personality_inventory as cli

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def files(tmp_path):
    snap = snapshot()
    paths = {}
    for name, value in (('snapshot', snap), ('universe', universe(snap)), ('classifications', classes()),
                        ('cutoff', START)):
        paths[name] = tmp_path / f'{name}.json'
        paths[name].write_text(json.dumps(value))
    paths['out'] = tmp_path / 'manifest.json'
    return paths


def preview_args(f, **extra):
    args = ['preview']
    for name in ('snapshot', 'universe', 'classifications', 'cutoff', 'out'):
        args += ['--' + name, str(f[name])]
    for k, v in extra.items():
        args += ['--' + k, str(v)]
    return args


@pytest.fixture
def no_db(monkeypatch):
    """Offline commands must not import a database driver or the store."""
    import builtins
    real = builtins.__import__

    def guarded(name, *a, **k):
        if name in ('psycopg', 'psycopg2') or name.endswith('lifecycle_store') or name == 'db.db_handler':
            raise AssertionError(f'offline command imported {name}')
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, '__import__', guarded)


def test_preview_writes_golden_manifest_offline(files, no_db):
    assert cli.main(preview_args(files)) == L.EXIT_OK
    manifest = json.loads(files['out'].read_text())
    assert manifest['schema'] == 'personality-lifecycle-v1'
    assert manifest['counts']['by_action'] == {'initialize': 5}
    assert L.validate_manifest(manifest)['valid']
    again = files['out'].with_name('again.json')
    files2 = dict(files, out=again)
    assert cli.main(preview_args(files2)) == L.EXIT_OK
    assert again.read_text() == files['out'].read_text()      # byte-identical replay


def test_unresolved_exit_3_still_writes_manifest(files, no_db):
    files['classifications'].write_text(json.dumps(dict(receipts=[])))
    assert cli.main(preview_args(files)) == L.EXIT_UNRESOLVED
    manifest = json.loads(files['out'].read_text())
    assert manifest['counts']['by_action'] == {'pending': 5}


def test_invalid_input_exit_2(files, no_db):
    files['cutoff'].write_text(json.dumps(dict(season=2026, phase='lunch', sequence=0)))
    assert cli.main(preview_args(files)) == L.EXIT_INVALID
    files['cutoff'].write_text(json.dumps(START))
    uni = json.loads(files['universe'].read_text())
    uni['league_id'] = 'OTHER'
    files['universe'].write_text(json.dumps(uni))
    assert cli.main(preview_args(files)) == L.EXIT_INVALID


def test_changed_version_or_input_invalidates_manifest(files, no_db, capsys):
    cli.main(preview_args(files))
    manifest = json.loads(files['out'].read_text())
    stale = dict(manifest, core_version='personality-core-v0')
    path = files['out'].with_name('stale.json')
    path.write_text(json.dumps(stale))
    assert cli.main(['validate', '--manifest', str(path)]) == L.EXIT_INVALID
    manifest['people'][0]['proposed_profile']['sliders']['drive'] = 10
    path.write_text(json.dumps(manifest))
    assert cli.main(['validate', '--manifest', str(path)]) == L.EXIT_INVALID      # hash no longer matches


def test_validate_rehash_and_compare(files, no_db):
    cli.main(preview_args(files))
    rehashed = files['out'].with_name('rehashed.json')
    assert cli.main(['validate', '--manifest', str(files['out']), '--rehash', '--out', str(rehashed)]) == 0
    diff = files['out'].with_name('diff.json')
    assert cli.main(['compare', '--before', str(files['out']), '--after', str(rehashed), '--out', str(diff)]) == 0
    assert json.loads(diff.read_text())['same']


def test_universe_command(files, no_db, tmp_path):
    out = tmp_path / 'u.json'
    assert cli.main(['universe', '--snapshot', str(files['snapshot']), '--out', str(out)]) == 0
    u = json.loads(out.read_text())
    assert u['league_id'] == 'LEAGUE-1' and u['universe_id'].startswith('mu:')
    assert u['initial_snapshot_sha256'] == L.snapshot_sha256(snapshot())


def test_exports_contain_no_dsn(files, no_db):
    cli.main(preview_args(files))
    text = files['out'].read_text()
    assert 'dsn' not in text.lower() and 'password' not in text.lower() and 'host=' not in text


def test_database_commands_require_explicit_dsn():
    with pytest.raises(SystemExit):
        cli.parser().parse_args(['export', '--universe', 'mu:x', '--out', 'x.json'])
    with pytest.raises(SystemExit):
        cli.parser().parse_args(['apply', '--manifest', 'm', '--review', 'r', '--expect-hash', 'h'])


def test_help_runs_without_database():
    out = subprocess.run([sys.executable, '-m', 'tools.personality_inventory', '--help'], cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and 'preview' in out.stdout
    assert 'psycopg' not in subprocess.run(
        [sys.executable, '-c', 'import sys, tools.personality_inventory; print(sorted(sys.modules))'],
        cwd=ROOT, capture_output=True, text=True, timeout=60).stdout
