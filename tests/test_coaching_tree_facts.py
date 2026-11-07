"""engines/coaching_tree_facts.py (COACHING_TREES_PLAN.md §2.4, U2) with a fake cursor: staff_seasons expansion and
clipping, fired flags, missing tables, and tree_context."""
import json

from engines import coaching_tree_facts as f


class FakeCursor:
    def __init__(self, hc=(), coord=(), fired=(), missing=(), boom=False):
        self.hc, self.coord, self.fired, self.missing, self.boom = list(hc), list(coord), list(fired), set(missing), boom
        self.sql, self._rows = [], []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if self.boom and 'q:hc' in sql:
            raise RuntimeError('relation does not exist')
        if 'q:regclass' in sql:
            self._rows = [(params[0] not in self.missing,)]
        elif 'q:fired' in sql:
            self._rows = self.fired
        elif 'q:hc' in sql:
            self._rows = self.hc
        elif 'q:coord' in sql:
            self._rows = self.coord
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


HC = [(1, 'Ann Head', 10, 'KC', 2013, None, 'HC')]
COORD = [(7, 'Bo Coord', 10, 'KC', 2024, 2027, 'OC'), (8, 'Cy D', 10, 'KC', 2026, 2026, 'DC')]


def test_staff_seasons_expands_and_clips():
    cur = FakeCursor(HC, COORD, fired=[(2027, 10, 'OC', 7)])
    rows = f.staff_seasons(cur, through=2028)
    hc = [r for r in rows if r['role'] == 'HC']
    assert [r['season'] for r in hc] == [2026, 2027, 2028]            # clipped to 2026..through, open = through
    oc = [r for r in rows if r['role'] == 'OC']
    assert [(r['season'], r['fired']) for r in oc] == [(2026, False), (2027, True)]
    assert {r['namespace'] for r in hc} == {'db-coach'} and oc[0]['namespace'] == 'db-coordinator'
    assert oc[0]['team'] == 'KC' and oc[0]['external_id'] == 7
    assert any('SAVEPOINT' in s for s in cur.sql) and any('RELEASE SAVEPOINT' in s for s in cur.sql)


def test_staff_seasons_missing_tables_read_empty():
    assert f.staff_seasons(FakeCursor(HC, COORD, missing={'coordinator_tenures'}), through=2028) == []
    rows = f.staff_seasons(FakeCursor(HC, COORD, missing={'coordinator_hot_seat'}), through=2027)
    assert not any(r['fired'] for r in rows)


def test_tree_context_is_none_on_a_missing_table_or_error(tmp_path):
    assert f.tree_context(FakeCursor(HC, COORD, missing={'coach_tenures'}), 'Bo Coord', through=2028,
                          directory=tmp_path) is None
    cur = FakeCursor(HC, COORD, boom=True)
    assert f.tree_context(cur, 'Bo Coord', through=2028, directory=tmp_path) is None
    assert any('ROLLBACK TO SAVEPOINT' in s for s in cur.sql)


def test_tree_context_from_simulated_tenures(tmp_path):
    ctx = f.tree_context(FakeCursor(HC, COORD), 'Bo Coord', through=2028, directory=tmp_path)
    assert ctx['line'] == 'Served 2 seasons under Ann Head.'
    assert ctx['mentors'][0]['basis'] == 'simulated'
    assert f.tree_context(FakeCursor(HC, COORD), 'Cy D', through=2028, directory=tmp_path) is None   # 1 season
    head = f.tree_context(FakeCursor(HC, COORD), 'Ann Head', through=2028, directory=tmp_path)
    assert head['proteges'][0]['name'] == 'Bo Coord'


def test_tree_graph_reads_files_and_tenures(tmp_path):
    (tmp_path / 'trees.json').write_text(json.dumps({'schema_version': 'coaching-trees-v1', 'kind': 'registry',
                                                     'trees': [], 'evidence': []}))
    g = f.tree_graph(FakeCursor(HC, COORD), through=2028, directory=tmp_path)
    assert g['head_coach_pks'] == frozenset({'p:ann head'}) and len(g['edges']) == 1 and g['registry'] == {}
