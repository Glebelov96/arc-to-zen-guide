"""
Tests for arc2zen.py. Standard library only:

    python3 -m unittest discover -s tests -v

The Zen fixture below mirrors the structure of a real Zen session saved in
October 2026 (folders made by hand in Zen), with made-up names and URLs.
"""
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import arc2zen as z  # noqa: E402

TOOL = str(ROOT / "arc2zen.py")
PRINCIPAL = "{\"3\":{}}"


# ── Fixtures ────────────────────────────────────────────────────────────────
def arc_fixture():
    """A small Arc sidebar: two Spaces, nested folders, an arc:// tab, Favorites."""
    items, spaces = [], []

    def tab(i, parent, url, title=None):
        items.append({"id": i, "parentID": parent, "title": title,
                      "data": {"tab": {"savedURL": url, "savedTitle": f"Saved {i}"}}})
        return i

    def folder(i, parent, title, children):
        items.append({"id": i, "parentID": parent, "title": title,
                      "childrenIds": children, "data": {"list": {}}})
        return i

    # Space "Personal": Reading (2 tabs) / Work > Plans (1 tab) + 1 tab / top tab / arc:// tab
    items.append({"id": "P-root", "childrenIds": ["f-read", "f-work", "t-top", "t-arc"], "data": {}})
    folder("f-read", "P-root", "Reading", ["t1", "t2"])
    tab("t1", "f-read", "https://example.com/a", "Article A")
    tab("t2", "f-read", "https://example.com/b")
    folder("f-work", "P-root", "Work", ["f-plans", "t4"])
    folder("f-plans", "f-work", "Plans", ["t3"])
    tab("t3", "f-plans", "https://example.com/plan")
    tab("t4", "f-work", "https://example.com/board")
    tab("t-top", "P-root", "https://example.com/top")
    tab("t-arc", "P-root", "arc://settings")
    spaces.append({"id": "S1", "title": "Personal",
                   "containerIDs": ["unpinned", "P-unp", "pinned", "P-root"]})
    # Space "Studio" uses the newer key and the dict-style label
    items.append({"id": "S-root", "childrenIds": ["t5"], "data": {}})
    tab("t5", "S-root", "https://example.org/studio")
    spaces.append({"id": "S2", "title": "Studio", "customInfo": {"iconType": {"emoji_v2": "🧧"}},
                   "newContainerIDs": [{"unpinned": {}}, "S-unp", {"pinned": {}}, "S-root"]})
    # Favorites, with a duplicate
    items.append({"id": "fav-root", "childrenIds": ["fv1", "fv2", "fv3"], "data": {}})
    tab("fv1", "fav-root", "https://mail.example.com/")
    tab("fv2", "fav-root", "https://cal.example.com/")
    tab("fv3", "fav-root", "https://mail.example.com/")
    return {"sidebar": {"containers": [
        {"global": {}},
        {"spaces": spaces, "items": items, "topAppsContainerIDs": ["fav-root"]}]}}


def zen_tab(sync, url, ws, group=None, empty=False, pinned=True):
    entry = {"url": url, "triggeringPrincipal_base64": PRINCIPAL}
    t = {"entries": [entry], "lastAccessed": 1, "pinned": pinned, "hidden": False,
         "zenWorkspace": ws, "zenSyncId": sync, "zenEssential": False,
         "zenDefaultUserContextId": None, "zenPinnedIcon": None, "zenIsEmpty": empty,
         "zenHasStaticIcon": False, "zenGlanceId": None, "zenIsGlance": False,
         "_zenPinnedInitialState": {"entry": entry, "image": None}, "_zenIsActiveTab": False,
         "zenLiveFolderItemId": None, "searchMode": None, "userContextId": 0,
         "attributes": {}, "index": 1, "userTypedValue": "", "userTypedClear": 0, "image": None}
    if group:
        t["groupId"] = group
    return t


def zen_fixture():
    """Shaped like a real Zen session with folders made by hand in Zen."""
    ws, ws2 = "{11111111-1111-4111-8111-111111111111}", "{22222222-2222-4222-8222-222222222222}"

    def folder(fid, name, parent, prev, empties):
        return {"pinned": True, "splitViewGroup": False, "id": fid, "name": name, "collapsed": False,
                "saveOnWindowClose": True, "parentId": parent, "prevSiblingInfo": prev,
                "emptyTabIds": empties, "userIcon": "", "workspaceId": ws}

    def group(fid, name):
        return {"pinned": True, "splitView": False, "id": fid, "name": name,
                "color": "zen-workspace-color", "collapsed": False, "saveOnWindowClose": True}

    return {
        "lastCollected": 1,
        "tabs": [
            zen_tab("e1", "about:blank", ws, "F1", empty=True),
            zen_tab("e2", "about:blank", ws, "F2", empty=True),
            zen_tab("a", "https://example.net/1", ws, "F2"),
            zen_tab("b", "https://example.net/2", ws, "F2"),
            zen_tab("e3", "about:blank", ws, "F3", empty=True),
            zen_tab("c", "https://example.net/3", ws, "F3"),
            zen_tab("open-1", "https://example.net/open", ws2, pinned=False),
            zen_tab("old-pin", "https://example.net/old", ws2),
        ],
        "folders": [
            folder("F1", "Inspiration", None, {"type": "start", "id": None}, ["e1", "e2", "e3"]),
            folder("F2", "Home", "F1", {"type": "tab", "id": "e1"}, ["e2"]),
            folder("F3", "Art", "F1", {"type": "group", "id": "F2"}, ["e3"]),
        ],
        "splitViewData": [],
        "groups": [group("F1", "Inspiration"), group("F2", "Home"), group("F3", "Art")],
        "spaces": [
            {"uuid": ws, "icon": "chrome://browser/skin/zen-icons/selectable/skull.svg",
             "name": "My Space", "theme": {"type": "gradient"}, "containerTabId": 0,
             "hasCollapsedPinnedTabs": False},
            {"uuid": ws2, "icon": "chrome://browser/skin/zen-icons/selectable/inbox.svg",
             "name": "Studio", "theme": {"type": "gradient"}, "containerTabId": 2,
             "hasCollapsedPinnedTabs": False},
        ],
    }


class Sandbox:
    """A fake computer: an Arc folder and a Zen folder with three profiles."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.arc = root / "arc"
        self.zen = root / "zen"
        (self.home / "Documents").mkdir(parents=True)
        self.arc.mkdir()
        (self.arc / "StorableSidebar.json").write_text(json.dumps(arc_fixture()), encoding="utf-8")
        (self.arc / "StorableSidebar.2026-10-01-000000-001.json").write_text("{}", encoding="utf-8")
        self._make_arc_user_data(self.arc / "User Data")
        profiles = self.zen / "Profiles"
        self.old = profiles / "aaaaaaaa.Default (release)"
        self.real = profiles / "bbbbbbbb.Default (release)-1"
        self.empty = profiles / "cccccccc.Default Profile"
        for p in (self.old, self.real, self.empty):
            p.mkdir(parents=True)
            (p / ".parentlock").touch()
        z.mozlz4_write(self.old / z.SESSION_FILE, {"lastCollected": 1, "tabs": [], "spaces": []})
        z.mozlz4_write(self.real / z.SESSION_FILE, zen_fixture())
        os.utime(self.old / z.SESSION_FILE, (1000, 1000))
        os.utime(self.real / z.SESSION_FILE, (2000, 2000))
        (self.zen / "installs.ini").write_text(
            "[A]\nDefault=Profiles/aaaaaaaa.Default (release)\n\n"
            "[B]\nDefault=Profiles/bbbbbbbb.Default (release)-1\n", encoding="utf-8")
        self.original = (self.real / z.SESSION_FILE).read_bytes()

    def _make_arc_user_data(self, ud):
        """A Chromium-shaped profile: History sqlite + one extension manifest."""
        import sqlite3
        default = ud / "Default"
        default.mkdir(parents=True)
        con = sqlite3.connect(default / "History")
        con.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
                    "visit_count INTEGER, hidden INTEGER, last_visit_time INTEGER)")
        now_us = int((time.time() + 11_644_473_600) * 1_000_000)
        con.executemany("INSERT INTO urls (url,title,visit_count,hidden,last_visit_time) VALUES (?,?,?,?,?)", [
            ("https://example.com/news", "The News", 5, 0, now_us),
            ("https://example.com/old", "Old Page", 1, 0, now_us - 86_400_000_000),
            ("https://secret.example/x", "Hidden", 2, 1, now_us),       # hidden → skipped
            ("arc://history", "Arc internal", 1, 0, now_us),             # non-http → skipped
        ])
        con.commit()
        con.close()
        mani = default / "Extensions" / "abcd" / "1.0_0" / "manifest.json"
        mani.parent.mkdir(parents=True)
        mani.write_text(json.dumps({"name": "uBlock Origin", "version": "1.0"}), encoding="utf-8")
        mani2 = default / "Extensions" / "efgh" / "2.0_0" / "manifest.json"
        mani2.parent.mkdir(parents=True)
        mani2.write_text(json.dumps({"name": "Some Niche Tool"}), encoding="utf-8")

    def env(self, **extra):
        e = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                 ARC2ZEN_ARC_DIR=str(self.arc), ARC2ZEN_ZEN_DIR=str(self.zen),
                 ARC2ZEN_ARC_USERDATA=str(self.arc / "User Data"), NO_COLOR="1")
        e.update(extra)
        return e

    def run(self, *args, stdin="", **extra):
        return subprocess.run([sys.executable, "-S", TOOL, *args], input=stdin, text=True,
                              capture_output=True, env=self.env(**extra))

    def session(self):
        return z.mozlz4_read(self.real / z.SESSION_FILE)

    def close(self):
        self.tmp.cleanup()


def space_id(data, name):
    return next(s["uuid"] for s in data["spaces"] if s["name"] == name)


# ── Tests ───────────────────────────────────────────────────────────────────
class TestLZ4(unittest.TestCase):
    def test_roundtrip_without_library(self):
        random.seed(3)
        samples = [b"", b"x", b"abc" * 4000, bytes(random.getrandbits(8) for _ in range(5000)),
                   json.dumps({"t": ["hello"] * 900, "u": "é🧧" * 50}).encode()]
        for raw in samples:
            stored = z._lz4_block_store(raw)
            if raw:
                self.assertEqual(z._lz4_block_decompress(stored, len(raw)), raw)

    def test_decodes_real_matches(self):
        # A hand-made block with a back-reference: "abcabcabc" = literals "abc" + match(off 3, len 6)
        block = bytes([0x32]) + b"abc" + struct.pack("<H", 3) + bytes([0x00])  # trailing empty literal run
        self.assertEqual(z._lz4_block_decompress(block, 9), b"abcabcabc")


class TestArc(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.arc = z.ArcSidebar(self.sb.arc / "StorableSidebar.json")

    def tearDown(self):
        self.sb.close()

    def test_spaces_order_and_counts(self):
        spaces = self.arc.spaces()
        self.assertEqual([s["name"] for s in spaces], ["Personal", "Studio"])
        self.assertEqual(z.count_tree(spaces[0]["tree"]), (3, 5))  # arc:// skipped
        self.assertEqual(spaces[1]["icon"], "🧧")
        tree = spaces[0]["tree"]
        self.assertEqual([n.get("name") or n["url"] for n in tree],
                         ["Reading", "Work", "https://example.com/top"])
        self.assertEqual(tree[0]["children"][0]["title"], "Article A")  # renamed title wins
        self.assertEqual(tree[1]["children"][0]["name"], "Plans")      # nesting kept

    def test_favorites_deduplicated(self):
        self.assertEqual([f["url"] for f in self.arc.favorites()],
                         ["https://mail.example.com/", "https://cal.example.com/"])


class TestZenFormat(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.prof = z.ZenProfile(self.sb.real)
        self.s = z.ZenSession(self.prof)
        arc = z.ArcSidebar(self.sb.arc / "StorableSidebar.json")
        self.spaces = arc.spaces()
        for sp in self.spaces:
            wid, _ = self.s.ensure_space(sp["name"], icon=sp["icon"])
            self.s.clear_space(wid)
            self.s.add_tree(sp["tree"], wid)
        self.d = self.s.data

    def tearDown(self):
        self.sb.close()

    def test_matches_native_folder_shape(self):
        native = zen_fixture()
        native_keys = set(native["folders"][0])
        for f in self.d["folders"]:
            self.assertEqual(set(f), native_keys)
        native_tab_keys = set(native["tabs"][0]) - {"_zenIsActiveTab", "groupId"}
        for t in self.d["tabs"]:
            self.assertTrue(native_tab_keys <= set(t), native_tab_keys - set(t))
        self.assertEqual({f["id"] for f in self.d["folders"]}, {g["id"] for g in self.d["groups"]})
        self.assertEqual(z.check_schema(self.d), [])

    def test_every_folder_starts_with_placeholder(self):
        tabs_by_sync = {t["zenSyncId"]: t for t in self.d["tabs"]}
        for f in self.d["folders"]:
            members = [t for t in self.d["tabs"] if t.get("groupId") == f["id"]]
            self.assertTrue(members[0]["zenIsEmpty"])
            self.assertEqual(members[0]["entries"][0]["url"], "about:blank")
            for e in f["emptyTabIds"]:
                self.assertTrue(tabs_by_sync[e]["zenIsEmpty"])
            # a parent lists its own and its sub-folders' placeholders
            kids = [c for c in self.d["folders"] if c["parentId"] == f["id"]]
            for c in kids:
                self.assertTrue(set(c["emptyTabIds"]) <= set(f["emptyTabIds"]))

    def test_sibling_chain(self):
        wid = space_id(self.d, "Personal")
        folders = {f["name"]: f for f in self.d["folders"] if f["workspaceId"] == wid}
        self.assertEqual(folders["Reading"]["prevSiblingInfo"], {"type": "start", "id": None})
        self.assertEqual(folders["Work"]["prevSiblingInfo"], {"type": "group", "id": folders["Reading"]["id"]})
        plans_prev = folders["Plans"]["prevSiblingInfo"]
        self.assertEqual(plans_prev["type"], "tab")
        self.assertEqual(plans_prev["id"], folders["Work"]["emptyTabIds"][0])  # after Work's placeholder

    def test_existing_content_respected(self):
        studio = space_id(self.d, "Studio")
        urls = [t["entries"][0]["url"] for t in self.d["tabs"] if t.get("zenWorkspace") == studio]
        self.assertIn("https://example.net/open", urls)      # unpinned tab kept
        self.assertNotIn("https://example.net/old", urls)    # old pin replaced
        self.assertIn("https://example.org/studio", urls)
        my_space = space_id(self.d, "My Space")
        self.assertEqual(sum(1 for f in self.d["folders"] if f["workspaceId"] == my_space), 3)
        # tabs in a Space with its own container open in that container
        for t in self.d["tabs"]:
            if t.get("zenWorkspace") == studio and t.get("pinned"):
                self.assertEqual(t["userContextId"], 2)


class TestSchemaCheck(unittest.TestCase):
    def test_unknown_folder_field_warns(self):
        d = zen_fixture()
        d["folders"][0]["brandNewField"] = 1
        self.assertTrue(any("brandNewField" in w for w in z.check_schema(d)))

    def test_mismatch_is_refused(self):
        d = zen_fixture()
        d["groups"].pop()
        with self.assertRaises(z.SchemaProblem):
            z.check_schema(d)

    def test_empty_session_ok(self):
        self.assertEqual(z.check_schema({"tabs": [], "spaces": []}), [])


class TestCommandLine(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_recommends_the_profile_in_use(self):
        out = self.sb.run("--list").stdout
        self.assertIn("bbbbbbbb.Default (release)-1  ← recommended", out)

    def test_migrate_then_undo(self):
        r = self.sb.run("--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Saved and checked", r.stdout)
        d = self.sb.session()
        self.assertEqual(sorted(s["name"] for s in d["spaces"]), ["My Space", "Personal", "Studio"])
        self.assertEqual(sum(1 for t in d["tabs"] if t.get("zenEssential")), 2)
        self.assertIn("separate-essentials", (self.sb.real / "user.js").read_text())
        r2 = self.sb.run("--yes")  # run again: same result, another backup
        self.assertEqual(r2.returncode, 0)
        self.assertEqual(len(self.sb.session()["folders"]), len(d["folders"]))
        self.assertEqual(len(z.ZenProfile(self.sb.real).backups()), 2)
        u = self.sb.run("--undo", stdin="1\ny\n")
        self.assertEqual(u.returncode, 0, u.stdout)
        self.assertEqual((self.sb.real / z.SESSION_FILE).read_bytes(), self.sb.original)

    def test_dry_run_and_decline_change_nothing(self):
        self.sb.run("--dry-run", "--yes")
        self.sb.run(stdin="\ny\nn\n")
        self.assertEqual((self.sb.real / z.SESSION_FILE).read_bytes(), self.sb.original)

    def test_unknown_format_stops(self):
        d = zen_fixture()
        d["folders"][0]["brandNewField"] = 1
        z.mozlz4_write(self.sb.real / z.SESSION_FILE, d)
        before = (self.sb.real / z.SESSION_FILE).read_bytes()
        r = self.sb.run("--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Nothing was changed", r.stdout)
        self.assertEqual((self.sb.real / z.SESSION_FILE).read_bytes(), before)

    def test_save_arc_then_migrate_without_arc(self):
        r = self.sb.run("--save-arc")
        self.assertEqual(r.returncode, 0, r.stdout)
        saved = list((self.sb.home / "Documents").glob("Arc backup */StorableSidebar.json"))
        self.assertEqual(len(saved), 1)
        self.assertTrue((saved[0].parent / "Arc pinned tabs.html").exists())
        self.assertTrue((saved[0].parent / "older copies").is_dir())
        r = self.sb.run("--yes", ARC2ZEN_ARC_DIR=str(self.sb.home / "no-arc-here"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("Using your saved copy", r.stdout)

    @unittest.skipIf(os.name == "nt", "uses POSIX file locks")
    def test_refuses_while_zen_runs(self):
        import fcntl
        fd = os.open(self.sb.real / ".parentlock", os.O_RDWR)
        fcntl.lockf(fd, fcntl.LOCK_EX)
        try:
            r = self.sb.run("--yes")
        finally:
            os.close(fd)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Zen is open", r.stdout)
        self.assertEqual((self.sb.real / z.SESSION_FILE).read_bytes(), self.sb.original)


def make_places(path, version=87):
    """A minimal but faithful Firefox/Zen places.sqlite: the three tables the
    importer touches, with the real FK and unique-guid index."""
    import sqlite3
    Path(path).unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE moz_origins (id INTEGER PRIMARY KEY, prefix TEXT NOT NULL,
        host TEXT NOT NULL, frecency INTEGER NOT NULL, recalc_frecency INTEGER NOT NULL,
        alt_frecency INTEGER, recalc_alt_frecency INTEGER NOT NULL);
      CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url LONGVARCHAR, title LONGVARCHAR,
        rev_host LONGVARCHAR, visit_count INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0 NOT NULL,
        typed INTEGER DEFAULT 0 NOT NULL, frecency INTEGER DEFAULT -1 NOT NULL,
        last_visit_date INTEGER, guid TEXT, foreign_count INTEGER DEFAULT 0 NOT NULL,
        url_hash INTEGER DEFAULT 0 NOT NULL, origin_id INTEGER,
        recalc_frecency INTEGER DEFAULT 0 NOT NULL, recalc_alt_frecency INTEGER DEFAULT 0 NOT NULL,
        FOREIGN KEY (origin_id) REFERENCES moz_origins(id));
      CREATE UNIQUE INDEX moz_places_guid_uniqueindex ON moz_places (guid);
      CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, from_visit INTEGER,
        place_id INTEGER, visit_date INTEGER, visit_type INTEGER, session INTEGER,
        source INTEGER NOT NULL);
    """)
    # one pre-existing page, so dedupe has something to hit
    con.execute("INSERT INTO moz_origins (prefix,host,frecency,recalc_frecency,recalc_alt_frecency)"
                " VALUES ('https://','example.com',100,0,1)")
    con.execute("INSERT INTO moz_places (url,title,rev_host,visit_count,guid,url_hash,origin_id,"
                "recalc_frecency,recalc_alt_frecency) VALUES "
                "('https://example.com/already','Already','moc.elpmaxe.',1,'existingGUID0',?,1,0,1)",
                (z.places_url_hash("https://example.com/already"),))
    con.execute(f"PRAGMA user_version = {version}")
    con.commit()
    con.close()


class TestPlacesHash(unittest.TestCase):
    def test_golden_values_from_a_real_zen_db(self):
        # (url, url_hash) pairs read from a real Zen places.sqlite (Oct 2026).
        for url, want in [
            ("https://www.mozilla.org/about/", 0x2b124d6d7c3d),
            ("https://www.mozilla.org/contribute/", 0x2b123edf2a3c),
            ("https://support.mozilla.org/products/firefox", 0x2b127843ecb6),
        ]:
            self.assertEqual(z.places_url_hash(url), want, url)


class TestImportHistory(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.places = Path(self.dir.name) / "places.sqlite"
        make_places(self.places)
        now_us = int((time.time() + 11_644_473_600) * 1_000_000)
        self.rows = [
            ("https://example.com/alpha", "Alpha", 3, now_us),
            ("https://news.example.org/", "News", 10, now_us - 3600_000_000),
            ("https://example.com/already", "Already (dup url)", 2, now_us),  # skipped
            ("https://example.com/beta", "Beta (same host)", 1, now_us),
        ]

    def tearDown(self):
        self.dir.cleanup()

    def _count(self, table):
        import sqlite3
        con = sqlite3.connect(self.places)
        try:
            return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        finally:
            con.close()

    def test_adds_and_verifies(self):
        added, skipped, backup = z.import_history_into_places(self.places, self.rows)
        self.assertEqual(added, 3)
        self.assertEqual(skipped, 1)       # the duplicate url
        self.assertTrue(backup.exists())
        import sqlite3
        con = sqlite3.connect(self.places)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        # same host reuses the one origin
        self.assertEqual(con.execute(
            "SELECT count(*) FROM moz_origins WHERE host='example.com'").fetchone()[0], 1)
        # every inserted place has a valid origin, a visit, correct url_hash, recalc flag
        for url in ("https://example.com/alpha", "https://news.example.org/", "https://example.com/beta"):
            h, oid, recalc = con.execute(
                "SELECT url_hash, origin_id, recalc_frecency FROM moz_places WHERE url=?", (url,)).fetchone()
            self.assertEqual(h, z.places_url_hash(url))
            self.assertIsNotNone(oid)
            self.assertEqual(recalc, 1)
            visits = con.execute("SELECT count(*) FROM moz_historyvisits WHERE place_id="
                                 "(SELECT id FROM moz_places WHERE url=?)", (url,)).fetchone()[0]
            self.assertEqual(visits, 1)
        con.close()

    def test_idempotent(self):
        z.import_history_into_places(self.places, self.rows)
        n = self._count("moz_places")
        added, skipped, _ = z.import_history_into_places(self.places, self.rows)
        self.assertEqual(added, 0)
        self.assertEqual(self._count("moz_places"), n)

    def test_unknown_schema_version_refused_and_db_untouched(self):
        make_places(self.places, version=250)
        before = self.places.read_bytes()
        with self.assertRaises(z.SchemaProblem):
            z.import_history_into_places(self.places, self.rows)
        self.assertEqual(self.places.read_bytes(), before)

    def test_force_allows_unknown_version(self):
        make_places(self.places, version=250)
        added, _skipped, _backup = z.import_history_into_places(self.places, self.rows, force=True)
        self.assertEqual(added, 3)

    def test_rollback_restores_on_error(self):
        # A row with a non-string url makes the INSERT raise partway through.
        # The whole DB must come back byte-for-byte from the backup.
        before = self.places.read_bytes()
        bad = [("https://good.example/1", "ok", 1, 1), (12345, "bad url", 1, 1)]
        with self.assertRaises(Exception):
            z.import_history_into_places(self.places, bad)
        self.assertEqual(self.places.read_bytes(), before)


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.hist = self.sb.arc / "User Data" / "Default" / "History"

    def tearDown(self):
        self.sb.close()

    def test_reads_only_visible_http_pages(self):
        rows = z.read_arc_history(self.hist)
        urls = [r[0] for r in rows]
        self.assertEqual(urls, ["https://example.com/news", "https://example.com/old"])  # newest first
        self.assertNotIn("https://secret.example/x", urls)  # hidden skipped
        self.assertTrue(all(not u.startswith("arc://") for u in urls))
        self.assertEqual(rows[0][1], "The News")
        self.assertRegex(rows[0][3], r"\d{4}-\d\d-\d\d \d\d:\d\d")

    def test_read_is_nondestructive(self):
        before = self.hist.read_bytes()
        z.read_arc_history(self.hist)
        self.assertEqual(self.hist.read_bytes(), before)

    def test_limit(self):
        self.assertEqual(len(z.read_arc_history(self.hist, limit=1)), 1)

    def test_extensions_listed_and_deduped(self):
        names = z.read_arc_extensions(self.sb.arc / "User Data")
        self.assertIn("uBlock Origin", names)
        self.assertIn("Some Niche Tool", names)
        self.assertEqual(len(names), len(set(n.lower() for n in names)))

    def test_cli_export_history(self):
        r = self.sb.run("--export-history", "--out", str(self.sb.home / "hout"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = self.sb.home / "hout"
        self.assertIn("The News", (out / "history.html").read_text())
        self.assertIn("visit_count", (out / "history.csv").read_text())

    def test_cli_report(self):
        r = self.sb.run("--report", "--out", str(self.sb.home / "report.html"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        html_text = (self.sb.home / "report.html").read_text()
        self.assertIn("uBlock Origin", html_text)
        self.assertIn("addons.mozilla.org", html_text)     # Firefox-equivalent hint
        self.assertIn("Passwords", html_text)
        self.assertIn("export", html_text.lower())
        self.assertNotIn("Login Data", html_text)          # we never reference the password store


if __name__ == "__main__":
    unittest.main()
