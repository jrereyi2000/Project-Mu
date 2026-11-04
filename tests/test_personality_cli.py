"""python -m personality subcommands (docs/in_progress/PERSONALITY_CORE_EXTENSIONS_PLAN.md §2.4).

Offline: database commands run against a fake connection injected through the CLI's connect(), with the
personality.store functions replaced. No real database is opened.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import personality.__main__ as cli
from personality import store
from personality.core import ConflictError, Identity, NotFoundError, PersonalityError, initialize

ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ['--universe', 'u', '--role', 'player', '--birth-key', 'k', '--season', '2026']


class FakeCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, rows=()):
        self.cur = FakeCursor(rows)
        self.committed = self.rolled_back = self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture
def conn(monkeypatch):
    fake = FakeConn()
    seen = []
    monkeypatch.setattr(cli, 'connect', lambda dsn: seen.append(dsn) or fake)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    fake.dsns = seen
    return fake


def profile(revision=0):
    p = initialize(Identity('u', 'player', 'k'), 'generated', 2026)
    p['revision'] = revision
    return p


def test_no_subcommand_is_the_preview_byte_for_byte(capsys):
    expected = json.dumps(initialize(Identity('u', 'player', 'k'), 'generated', 2026), sort_keys=True, indent=2) + '\n'
    assert cli.main(PREVIEW) == 0
    assert capsys.readouterr().out == expected
    assert cli.main(['preview'] + PREVIEW) == 0
    assert capsys.readouterr().out == expected
    run = subprocess.run([sys.executable, '-m', 'personality'] + PREVIEW, cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0 and run.stdout == expected


def test_preview_out_path(tmp_path):
    out = tmp_path / 'p' / 'profile.json'
    assert cli.main(PREVIEW + ['--out', str(out)]) == 0
    assert json.loads(out.read_text())['birth_key'] == 'k'


def test_help_lists_subcommands():
    run = subprocess.run([sys.executable, '-m', 'personality', '--help'], cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0
    for name in ('preview', 'override', 'adopt', 'link', 'history', 'as-of'):
        assert name in run.stdout


def override_argv(*extra):
    return ['override', '--universe', 'u', '--role', 'player', '--birth-key', 'k', '--season', '2027',
            '--tick', '4', '--set', 'drive=6', '--set', 'warmth=4', '--add-trait', 'GUARDED',
            '--remove-trait', 'LOYAL', '--reason', 'JR edit', '--override-id', 'o1', '--dsn', 'dbname=madden_test'] + list(extra)


def test_override_builds_the_request_and_commits(conn, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(store, 'read', lambda cur, ident: profile(revision=2))
    monkeypatch.setattr(store, 'override', lambda cur, ident, req: calls.append((ident, req)) or profile(revision=3))
    assert cli.main(override_argv()) == 0
    ident, req = calls[0]
    assert ident == Identity('u', 'player', 'k')
    assert req == dict(override_id='o1', actor='JR', reason='JR edit', season=2027, tick=4, expected_revision=2,
                       sliders={'drive': 6, 'warmth': 4}, add_traits=['GUARDED'], remove_traits=['LOYAL'])
    assert conn.committed and not conn.rolled_back and conn.closed
    out = json.loads(capsys.readouterr().out)
    assert out['kind'] == 'override' and out['dry_run'] is False and out['after']['revision'] == 3


def test_dry_run_rolls_back(conn, monkeypatch, capsys):
    monkeypatch.setattr(store, 'read', lambda cur, ident: profile())
    monkeypatch.setattr(store, 'override', lambda cur, ident, req: profile(revision=1))
    assert cli.main(override_argv('--dry-run')) == 0
    assert conn.rolled_back and not conn.committed
    assert json.loads(capsys.readouterr().out)['dry_run'] is True


@pytest.mark.parametrize('error,code', [(ConflictError('stale profile revision'), 4),
                                        (NotFoundError('absent'), 3),
                                        (PersonalityError('trait not eligible'), 2)])
def test_exit_codes_and_rollback_on_errors(conn, monkeypatch, error, code):
    monkeypatch.setattr(store, 'read', lambda cur, ident: profile())

    def boom(cur, ident, req):
        raise error
    monkeypatch.setattr(store, 'override', boom)
    assert cli.main(override_argv()) == code
    assert conn.rolled_back and not conn.committed


def test_missing_profile_and_bad_set_and_alias(conn, monkeypatch):
    monkeypatch.setattr(store, 'read', lambda cur, ident: None)
    assert cli.main(override_argv()) == 3
    monkeypatch.setattr(store, 'read', lambda cur, ident: profile())
    assert cli.main(override_argv('--set', 'drive')) == 2
    monkeypatch.setattr(store, 'resolve_alias', lambda *a: None)
    argv = override_argv()
    argv[argv.index('--birth-key'):argv.index('--birth-key') + 2] = ['--alias', 'save-pgid:123']
    assert cli.main(argv) == 3
    argv[argv.index('--alias') + 1] = 'nocolon'
    assert cli.main(argv) == 2


def test_dsn_guard_and_missing_dsn(conn, monkeypatch):
    for dsn in ('dbname=madden_2036', 'host=localhost dbname=postgres user=postgres'):
        argv = override_argv()
        argv[argv.index('--dsn') + 1] = dsn
        assert cli.main(argv) == 2
    argv = override_argv()
    del argv[argv.index('--dsn'):argv.index('--dsn') + 2]
    assert cli.main(argv) == 2
    assert conn.dsns == []
    monkeypatch.setenv('DATABASE_URL', 'dbname=madden_2036')
    assert cli.main(argv) == 2
    assert conn.dsns == []


def test_adopt_writers_and_gms_use_the_planned_origins(conn, monkeypatch, capsys):
    calls = []

    def adopt(cur, universe, role, legacy_id, origin, season, *, reviewer, reason):
        calls.append((role, legacy_id, origin, season, reviewer, reason))
        return dict(revision=0, sliders={}, traits=[])
    monkeypatch.setattr(store, 'adopt_legacy', adopt)
    conn.cur.rows = [('ESPN',), ('KIMES',)]
    assert cli.main(['adopt', '--universe', 'u', '--writers', '--season', '2026', '--reason', 'adopt',
                     '--dsn', 'dbname=madden_test']) == 0
    assert calls == [('writer', 'ESPN', 'real', 2026, 'JR', 'adopt'), ('writer', 'KIMES', 'real', 2026, 'JR', 'adopt')]
    assert conn.cur.sql[0][1] == ('writer',)
    assert json.loads(capsys.readouterr().out)['count'] == 2
    calls.clear()
    conn.cur.rows = [('7',)]
    assert cli.main(['adopt', '--universe', 'u', '--gms', '--season', '2026', '--reason', 'adopt',
                     '--dsn', 'dbname=madden_test', '--dry-run']) == 0
    assert calls == [('gm', '7', 'generated', 2026, 'JR', 'adopt')] and conn.rolled_back
    assert cli.main(['adopt', '--universe', 'u', '--legacy-id', 'X', '--season', '2026', '--reason', 'r',
                     '--dsn', 'dbname=madden_test']) == 2       # --legacy-id needs --role
    assert cli.main(['adopt', '--universe', 'u', '--role', 'coach', '--legacy-id', 'X', '--season', '2026',
                     '--reason', 'r', '--dsn', 'dbname=madden_test']) == 2   # no default origin for coaches


def test_link_parses_role_keys(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(store, 'link_role_change',
                        lambda cur, a, b, season, *, tick, reason: calls.append((a, b, season, tick, reason)) or profile())
    assert cli.main(['link', '--universe', 'u', '--from', 'player:k', '--to', 'coach:person:k', '--season', '2034',
                     '--tick', '2', '--reason', 'retired', '--dsn', 'dbname=madden_test']) == 0
    assert calls == [(Identity('u', 'player', 'k'), Identity('u', 'coach', 'person:k'), 2034, 2, 'retired')]
    assert cli.main(['link', '--universe', 'u', '--from', 'player', '--to', 'coach:x', '--season', '2034',
                     '--reason', 'r', '--dsn', 'dbname=madden_test']) == 2


def test_history_and_as_of_never_commit(conn, monkeypatch, capsys):
    monkeypatch.setattr(store, 'history', lambda cur, ident: [dict(kind='adoption', season=2026, seq=1)])
    assert cli.main(['history', '--universe', 'u', '--role', 'gm', '--legacy-id', '7', '--dsn', 'dbname=madden_test']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['persona_id'] == '7' and out['receipts'][0]['kind'] == 'adoption'
    assert conn.rolled_back and not conn.committed
    monkeypatch.setattr(store, 'read_as_of', lambda cur, ident, season, tick: None)
    assert cli.main(['as-of', '--universe', 'u', '--role', 'gm', '--legacy-id', '7', '--season', '2026',
                     '--tick', '3', '--dsn', 'dbname=madden_test']) == 3
    monkeypatch.setattr(store, 'read_as_of', lambda cur, ident, season, tick: profile())
    assert cli.main(['as-of', '--universe', 'u', '--role', 'gm', '--legacy-id', '7', '--season', '2026',
                     '--dsn', 'dbname=madden_test']) == 0
    assert not conn.committed
