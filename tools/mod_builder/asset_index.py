"""Search over the game's asset names, read from MMC Editor's cache (MOD_BUILDER_PLAN §2.3).

`madden27.cache` is `FROSTY\\0\\x02`, a version, the game build, a 20-byte layout fingerprint
(MOD_REVERSE_ENGINEERING §5), then one plain zstd stream (~575 MB). The stream's layout is not documented;
this is what it holds as far as the index needs (read off build 3468304, 2026-10-30, and checked by walking
every record of that cache to the end of the res section):

    u32 × 5          superbundle, bundle, ebx, res, chunk counts
    superbundles     cstr name
    bundles          cstr name, u32
    cas files        u32 n, n × cstr path
    ebx              cstr name, sha1[20], i64 size, i64 original size, i32 location, u8 inline,
                     cstr type, guid[16], u8 has_extra (+18 bytes), i32 n + n × u32 bundles,
                     i32 n + n × guid dependencies
    res              cstr name, sha1[20], i64, i64, i32, u8, u64 rid, u32 res type, i32 n + n meta bytes,
                     u8 has_extra (+18 bytes), i32 n + n × u32 bundles
    chunks           (GUIDs only, not indexed)

Names are pulled out by walking those records in a bounded buffer (never the whole stream in memory). If a
record doesn't fit the layout (a future editor version), the rest of the stream is scanned heuristically for
printable asset paths instead, with kind "unknown". Legacy file paths come from the sibling
`madden27_legacysha1.cache` (`\\0SFL`, u32, then cstr path + 40 bytes each) when it is there.

The index is SQLite, data/mod_builds/_index/madden27_assets.sqlite, table
`assets(name, kind, type)` with kind ebx / res / bundle / legacy (/ unknown); type is the ebx type name or
the res type name (fbmod_inspect.RES_TYPES, else hex), NULL otherwise. It is rebuilt when the cache's size
or mtime changes. MU_MMC_EDITOR_DIR overrides the editor folder.
"""
import os
import re
import sqlite3
import struct
import sys

from tools.fbmod_inspect import RES_TYPES
from tools.mod_builder import builds_dir

DEFAULT_EDITOR_DIR = "/mnt/c/Madden27Modding/MMC_Modding_Tools_v1.1.0.7/MMC_Editor_v1.1.0.7"
CACHE_MAGIC = b"FROSTY\x00\x02"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
LEGACY_MAGIC = b"\x00SFL"
CHUNK = 8 << 20
MAX_NAME = 4096
BATCH = 50_000
SCHEMA_VERSION = "1"
PRINTABLE = re.compile(rb"^[\x20-\x7e]+$")
HEURISTIC = re.compile(rb"([A-Za-z0-9_][\x20-\x7e]{2,1023})\x00")


class LayoutError(ValueError):
    """A record in the cache stream doesn't fit the known layout."""


def editor_dir():
    return os.environ.get("MU_MMC_EDITOR_DIR") or DEFAULT_EDITOR_DIR


def default_cache_path():
    return os.path.join(editor_dir(), "Caches", "madden27.cache")


def default_index_path():
    return os.path.join(builds_dir(), "_index", "madden27_assets.sqlite")


def legacy_cache_path(cache_path):
    stem, ext = os.path.splitext(cache_path)
    return f"{stem}_legacysha1{ext}"


class _Reader:
    """Sequential reads over a stream, holding at most a few MB at a time."""

    def __init__(self, stream):
        self.stream, self.buf, self.pos, self.eof, self.base = stream, b"", 0, False, 0

    def _fill(self, n):
        while len(self.buf) - self.pos < n and not self.eof:
            data = self.stream.read(CHUNK)
            if not data:
                self.eof = True
                break
            self.base += self.pos
            self.buf = self.buf[self.pos:] + data
            self.pos = 0

    @property
    def offset(self):
        return self.base + self.pos

    def read(self, n):
        self._fill(n)
        if len(self.buf) - self.pos < n:
            raise LayoutError(f"stream ends at {self.offset} inside a record")
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        return out

    def skip(self, n):
        self.read(n)

    def u8(self):
        return self.read(1)[0]

    def i32(self):
        return struct.unpack("<i", self.read(4))[0]

    def u32(self):
        return struct.unpack("<I", self.read(4))[0]

    def count(self, limit=1 << 20):
        n = self.i32()
        if not 0 <= n <= limit:
            raise LayoutError(f"bad count {n} at {self.offset - 4}")
        return n

    def cstr(self, check=True):
        end = self.buf.find(b"\0", self.pos)
        while end < 0:
            if len(self.buf) - self.pos > MAX_NAME or self.eof:
                raise LayoutError(f"no string end after {self.offset}")
            self._fill(len(self.buf) - self.pos + CHUNK)
            end = self.buf.find(b"\0", self.pos)
        raw = self.buf[self.pos:end]
        if check and (not raw or len(raw) > MAX_NAME or not PRINTABLE.match(raw)):
            raise LayoutError(f"not a name at {self.offset}: {raw[:40]!r}")
        self.pos = end + 1
        return raw.decode("latin-1")

    def rest(self):
        """The remaining bytes, in CHUNK pieces (the unread part of the buffer first)."""
        if self.pos < len(self.buf):
            yield self.buf[self.pos:]
        self.buf, self.pos = b"", 0
        while not self.eof:
            data = self.stream.read(CHUNK)
            if not data:
                self.eof = True
                break
            yield data


def open_cache_stream(cache_path):
    """(header dict, zstd stream reader) for a madden27.cache; the caller closes the file."""
    import zstandard
    f = open(cache_path, "rb")
    head = f.read(256)
    if head[:8] != CACHE_MAGIC:
        f.close()
        raise ValueError(f"{cache_path}: not an MMC Editor cache (no FROSTY\\0\\x02 magic)")
    start = head.find(ZSTD_MAGIC, 8)
    if start < 0:
        f.close()
        raise ValueError(f"{cache_path}: no zstd stream in the header")
    version, = struct.unpack_from("<I", head, 8)
    build, = struct.unpack_from("<I", head, start - 24) if start >= 32 else (None,)
    f.seek(start)
    reader = zstandard.ZstdDecompressor().stream_reader(f, read_size=1 << 20)
    return {"version": version, "build": build, "zstd_offset": start}, f, reader


def _res_type_name(rtype):
    return RES_TYPES.get(rtype, f"0x{rtype:08X}")


def iter_structured(r):
    """(name, kind, type) for every bundle, ebx and res record (superbundles and cas paths are skipped)."""
    n_sb, n_bundles, n_ebx, n_res, _n_chunks = (r.u32() for _ in range(5))
    for _ in range(n_sb):
        r.cstr()
    for _ in range(n_bundles):
        yield r.cstr(), "bundle", None
        r.skip(4)
    for _ in range(r.count()):
        r.cstr()
    for _ in range(n_ebx):
        name = r.cstr()
        r.skip(41)
        etype = r.cstr()
        r.skip(16)
        if r.u8():
            r.skip(18)
        r.skip(4 * r.count())
        r.skip(16 * r.count())
        yield name, "ebx", etype
    for _ in range(n_res):
        name = r.cstr()
        r.skip(41 + 8)
        rtype = r.u32()
        r.skip(r.count(1 << 16))
        if r.u8():
            r.skip(18)
        r.skip(4 * r.count())
        yield name, "res", _res_type_name(rtype)


def iter_heuristic(chunks):
    """Printable, NUL-terminated strings with a '/' in them, from a stream of byte chunks."""
    tail = b""
    for data in chunks:
        buf = tail + data
        cut = max(0, len(buf) - 2 * MAX_NAME)
        last = 0
        for m in HEURISTIC.finditer(buf):
            if m.start() >= cut:
                break
            s = m.group(1)
            if b"/" in s:
                yield s.decode("latin-1"), "unknown", None
            last = m.end()
        tail = buf[max(cut, last):]
    for m in HEURISTIC.finditer(tail):
        if b"/" in m.group(1):
            yield m.group(1).decode("latin-1"), "unknown", None


def iter_cache(cache_path, log=None):
    """(name, kind, type) for every asset name in the cache stream."""
    _head, f, stream = open_cache_stream(cache_path)
    try:
        r = _Reader(stream)
        try:
            for row in iter_structured(r):
                yield row
        except LayoutError as e:
            if log:
                log(f"cache layout not recognised ({e}); scanning the rest for asset paths")
            yield from iter_heuristic(r.rest())
    finally:
        f.close()


def iter_legacy(path):
    """(path, 'legacy', None) from a *_legacysha1.cache: magic, u32, then cstr + 40 bytes per file."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != LEGACY_MAGIC:
        return
    pos = 8
    while pos < len(data):
        end = data.find(b"\0", pos)
        if end < 0 or not PRINTABLE.match(data[pos:end] or b"\x00"):
            return
        yield data[pos:end].decode("latin-1"), "legacy", None
        pos = end + 1 + 40


def _stamp(path):
    st = os.stat(path)
    return f"{st.st_size}:{st.st_mtime_ns}"


def _stamps(cache_path):
    legacy = legacy_cache_path(cache_path)
    return {"schema": SCHEMA_VERSION, "cache": os.path.abspath(cache_path), "cache_stamp": _stamp(cache_path),
            "legacy_stamp": _stamp(legacy) if os.path.exists(legacy) else ""}


def build_index(cache_path=None, out_path=None, log=None):
    """Write the SQLite index for a cache; returns the number of rows."""
    cache_path = cache_path or default_cache_path()
    out_path = out_path or default_index_path()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    stamps = _stamps(cache_path)
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("CREATE TABLE assets (name TEXT NOT NULL, kind TEXT NOT NULL, type TEXT, "
                    "PRIMARY KEY (name, kind)) WITHOUT ROWID")
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        rows, total = [], 0

        def flush():
            nonlocal rows, total
            con.executemany("INSERT OR IGNORE INTO assets VALUES (?, ?, ?)", rows)
            total += len(rows)
            rows = []

        sources = [iter_cache(cache_path, log)]
        legacy = legacy_cache_path(cache_path)
        if os.path.exists(legacy):
            sources.append(iter_legacy(legacy))
        for source in sources:
            for row in source:
                rows.append(row)
                if len(rows) >= BATCH:
                    flush()
        flush()
        con.executemany("INSERT INTO meta VALUES (?, ?)", stamps.items())
        con.commit()
        count = con.execute("SELECT count(*) FROM assets").fetchone()[0]
    finally:
        con.close()
    os.replace(tmp, out_path)
    return count


def _meta(index_path):
    try:
        con = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        try:
            return dict(con.execute("SELECT key, value FROM meta"))
        finally:
            con.close()
    except sqlite3.Error:
        return {}


def is_stale(cache_path, index_path):
    return not os.path.exists(index_path) or _meta(index_path) != _stamps(cache_path)


def ensure_index(cache_path=None, index_path=None, log=None):
    """The index path, (re)built first when it is missing or the cache changed."""
    cache_path = cache_path or default_cache_path()
    index_path = index_path or default_index_path()
    if not os.path.exists(cache_path):
        if os.path.exists(index_path):
            return index_path
        raise FileNotFoundError(f"no MMC Editor cache at {cache_path} (set MU_MMC_EDITOR_DIR)")
    if is_stale(cache_path, index_path):
        if log:
            log(f"building the asset index from {cache_path} (a few minutes the first time)...")
        n = build_index(cache_path, index_path, log)
        if log:
            log(f"indexed {n} names into {index_path}")
    return index_path


def _like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def find(pattern, kind=None, limit=50, index_path=None, cache_path=None, log=None):
    """Assets whose name matches `pattern`: a glob (* ? [) on the whole name, else a substring; case-blind."""
    index_path = ensure_index(cache_path, index_path, log)
    if any(c in pattern for c in "*?["):
        where, arg = "lower(name) GLOB ?", pattern.lower()
    else:
        where, arg = "name LIKE ? ESCAPE '\\'", f"%{_like_escape(pattern)}%"
    sql = f"SELECT name, kind, type FROM assets WHERE {where}"
    args = [arg]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    sql += " ORDER BY length(name), name, kind LIMIT ?"
    args.append(int(limit))
    con = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    try:
        return [{"name": n, "kind": k, "type": t} for n, k, t in con.execute(sql, args)]
    finally:
        con.close()


def _log(msg):
    print(msg, file=sys.stderr, flush=True)
