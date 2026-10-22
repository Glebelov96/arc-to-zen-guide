#!/usr/bin/env python3
"""
arc2zen — move your Arc sidebar (Spaces, nested folders, pinned tabs,
Favorites) into Zen Browser, in one command. macOS and Windows.

    python3 arc2zen.py              # guided migration
    python3 arc2zen.py --save-arc   # keep a copy of your Arc sidebar for later
    python3 arc2zen.py --list       # show what was found, change nothing
    python3 arc2zen.py --dry-run    # show the plan, change nothing
    python3 arc2zen.py --undo       # put Zen back the way it was
    python3 arc2zen.py --bookmarks arc.html   # just export a bookmarks file

(On Windows, use `py` instead of `python3`.)

What it does, in order:
  1. Reads Arc's sidebar file (StorableSidebar.json), or a copy saved with --save-arc.
  2. Finds every Zen profile and picks the one Zen actually uses
     (asks you if it isn't obvious).
  3. Refuses to continue while Zen is running.
  4. Checks Zen's session file is in a format it knows; stops otherwise.
  5. Makes a timestamped backup that later runs never overwrite.
  6. Rebuilds each Arc Space as a Zen Space with the same folders and pins,
     written the way Zen itself writes folders.
  7. Reads the result back; if anything is off, restores the backup.

No installs needed: works with a standard Python 3.8+.

Guide and source: https://github.com/Glebelov96/arc-to-zen-guide

---------------------------------------------------------------------------
Parts of the Zen session builders are adapted from arc-to-zen-migration
(https://github.com/shub-rajput/arc-to-zen-migration), used under the MIT
License:

  MIT License. Copyright (c) shub-rajput.
  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:
  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.
  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
  SOFTWARE.
---------------------------------------------------------------------------
"""
import argparse
import configparser
import datetime
import errno
import html
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path

VERSION = "1.2.0"

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
RUN = "py arc2zen.py" if IS_WINDOWS else "python3 arc2zen.py"

SESSION_FILE = "zen-sessions.jsonlz4"
BACKUP_TAG = ".arc2zen-"          # zen-sessions.jsonlz4.arc2zen-20261022-231500-01.bak
MOZLZ4_MAGIC = b"mozLz40\x00"
ESSENTIALS_PREF = 'user_pref("zen.workspaces.separate-essentials", false);'
SAVE_DIR_PREFIX = "Arc backup "

# The folder shape Zen writes today (captured from a real session, Oct 2026).
# If Zen's own folders carry fields beyond these, its format has moved on and
# we stop instead of guessing.
KNOWN_FOLDER_KEYS = {
    "pinned", "splitViewGroup", "id", "name", "collapsed", "saveOnWindowClose",
    "parentId", "prevSiblingInfo", "emptyTabIds", "userIcon", "workspaceId",
}
REQUIRED_FOLDER_KEYS = {"id", "workspaceId"}
KNOWN_SPACE_KEYS = {"uuid", "name", "icon", "theme", "containerTabId",
                    "hasCollapsedPinnedTabs", "position"}


# ─────────────────────────────────────────────────────────────────────────────
# Where things live
# ─────────────────────────────────────────────────────────────────────────────
def home():
    return Path.home()


def arc_dir_candidates():
    if os.environ.get("ARC2ZEN_ARC_DIR"):  # for tests and unusual setups
        return [Path(os.environ["ARC2ZEN_ARC_DIR"])]
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", home() / "AppData" / "Local")) / "Packages"
        return sorted(base.glob("TheBrowserCompany.Arc*/LocalCache/Local/Arc"))
    return [home() / "Library" / "Application Support" / "Arc"]


def zen_dir():
    if os.environ.get("ARC2ZEN_ZEN_DIR"):
        return Path(os.environ["ARC2ZEN_ZEN_DIR"])
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA", home() / "AppData" / "Roaming")) / "zen"
    if IS_MAC:
        return home() / "Library" / "Application Support" / "zen"
    return home() / ".zen"


def documents_dir():
    d = home() / "Documents"
    return d if d.is_dir() else home()


def arc_user_data():
    """Arc's Chromium 'User Data' folder, next to the sidebar file.
    Holds History and Bookmarks (plain SQLite/JSON — not passwords)."""
    if os.environ.get("ARC2ZEN_ARC_USERDATA"):
        return Path(os.environ["ARC2ZEN_ARC_USERDATA"])
    for d in arc_dir_candidates():
        ud = d / "User Data"
        if ud.is_dir():
            return ud
    return None


# Common Arc/Chrome extensions → where to find a Firefox/Zen equivalent.
# A hint, not an install step; Zen uses Firefox add-ons from addons.mozilla.org.
EXTENSION_HINTS = {
    "ublock origin": "uBlock Origin — on addons.mozilla.org, same name",
    "bitwarden": "Bitwarden — on addons.mozilla.org, same name",
    "1password": "1Password — on addons.mozilla.org, same name",
    "dark reader": "Dark Reader — on addons.mozilla.org, same name",
    "grammarly": "Grammarly — on addons.mozilla.org, same name",
    "lastpass": "LastPass — on addons.mozilla.org, same name",
    "react developer tools": "React Developer Tools — on addons.mozilla.org",
    "metamask": "MetaMask — on addons.mozilla.org, same name",
    "honey": "Honey — on addons.mozilla.org, same name",
    "notion web clipper": "Notion Web Clipper — on addons.mozilla.org",
}


# ─────────────────────────────────────────────────────────────────────────────
# Terminal output
# ─────────────────────────────────────────────────────────────────────────────
if IS_WINDOWS:
    os.system("")  # turns on colour codes in the Windows console
_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t): return _c("1", t)
def dim(t): return _c("2", t)
def green(t): return _c("32", t)
def yellow(t): return _c("33", t)
def red(t): return _c("31", t)


def step(n, text):
    print(f"\n{bold(f'[{n}]')} {bold(text)}")


def ok(text): print(f"    {green('✓')} {text}")
def info(text): print(f"    {text}")
def warn(text): print(f"    {yellow('!')} {text}")


def fail(text, hint=None):
    print(f"\n{red('✗')} {text}")
    if hint:
        print(f"  {hint}")
    sys.exit(1)


class Prompt:
    """Interactive questions; answers are automatic with --yes."""

    def __init__(self, assume_yes=False):
        self.assume_yes = assume_yes

    def yes_no(self, question, default=True):
        if self.assume_yes:
            return default
        hint = "Y/n" if default else "y/N"
        while True:
            try:
                ans = input(f"    {question} [{hint}] ").strip().lower()
            except EOFError:
                return default
            if not ans:
                return default
            if ans in ("y", "yes"):
                return True
            if ans in ("n", "no"):
                return False

    def choose(self, question, count, default):
        """Pick 1..count; returns a 0-based index."""
        if self.assume_yes:
            return default
        while True:
            try:
                ans = input(f"    {question} [{default + 1}] ").strip()
            except EOFError:
                return default
            if not ans:
                return default
            if ans.isdigit() and 1 <= int(ans) <= count:
                return int(ans) - 1


# ─────────────────────────────────────────────────────────────────────────────
# mozLz4: Firefox/Zen's compressed JSON. Pure Python, no liblz4 needed.
# ─────────────────────────────────────────────────────────────────────────────
def _lz4_block_decompress(src, expected_size):
    dst = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]
        i += lit
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0 or offset > len(dst):
            raise ValueError("corrupt LZ4 data (bad offset)")
        mlen = token & 15
        if mlen == 15:
            while True:
                b = src[i]
                i += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(dst) - offset
        if offset >= mlen:
            dst += dst[start:start + mlen]
        else:  # overlapping match = repeating pattern
            pattern = bytes(dst[start:])
            dst += (pattern * (mlen // offset + 1))[:mlen]
    if len(dst) != expected_size:
        raise ValueError(f"corrupt LZ4 data (got {len(dst)} bytes, expected {expected_size})")
    return bytes(dst)


def _lz4_block_store(raw):
    """A valid LZ4 block holding the data as literals (no compression).
    Zen reads it fine and re-compresses the file the next time it saves."""
    n = len(raw)
    out = bytearray()
    if n < 15:
        out.append(n << 4)
    else:
        out.append(0xF0)
        rest = n - 15
        while rest >= 255:
            out.append(255)
            rest -= 255
        out.append(rest)
    out += raw
    return bytes(out)


def mozlz4_read(path):
    data = Path(path).read_bytes()
    if data[:8] != MOZLZ4_MAGIC:
        raise ValueError(f"{path} is not a mozLz4 file")
    size = struct.unpack("<I", data[8:12])[0]
    body = data[12:]
    try:  # use the fast C library if it happens to be installed
        import lz4.block  # type: ignore
        raw = lz4.block.decompress(body, uncompressed_size=size)
    except ImportError:
        raw = _lz4_block_decompress(body, size)
    return json.loads(raw.decode("utf-8"))


def mozlz4_write(path, obj):
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    try:
        import lz4.block  # type: ignore
        body = lz4.block.compress(raw, store_size=False)
    except ImportError:
        body = _lz4_block_store(raw)
    tmp = Path(str(path) + ".arc2zen-tmp")
    with open(tmp, "wb") as f:
        f.write(MOZLZ4_MAGIC + struct.pack("<I", len(raw)) + body)
    os.replace(tmp, path)  # atomic: never leaves a half-written session


# ─────────────────────────────────────────────────────────────────────────────
# Arc: read the sidebar
# ─────────────────────────────────────────────────────────────────────────────
class ArcSidebar:
    def __init__(self, path):
        self.path = Path(path)
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        containers = data.get("sidebar", {}).get("containers", [])
        main = next((c for c in containers
                     if isinstance(c, dict) and "spaces" in c and "items" in c), None)
        if main is None:
            raise ValueError("this doesn't look like Arc's StorableSidebar.json")
        self._main = main
        self.items = {it["id"]: it for it in main["items"] if isinstance(it, dict) and "id" in it}

    @staticmethod
    def _pairs(ids):
        """Arc stores containers as a flat [label, id, label, id, ...] list,
        where a label is either a string or a {"pinned": {}} style dict."""
        out = {}
        for i in range(len(ids) - 1):
            label, value = ids[i], ids[i + 1]
            if isinstance(value, str):
                if isinstance(label, str):
                    out.setdefault(label, value)
                elif isinstance(label, dict):
                    for k in label:
                        out.setdefault(k, value)
        return out

    def _children(self, node_id):
        node = self.items.get(node_id)
        if node and node.get("childrenIds") is not None:
            return [self.items[k] for k in node["childrenIds"] if k in self.items]
        return [it for it in self.items.values() if it.get("parentID") == node_id]

    def _build(self, node):
        data = node.get("data") or {}
        if "tab" in data:
            tab = data["tab"] or {}
            url = tab.get("savedURL") or ""
            if not url or url.startswith("arc://"):
                return None
            title = node.get("title") or tab.get("savedTitle") or url
            return {"type": "tab", "url": url, "title": title}
        if "list" in data or (node.get("title") and "childrenIds" in node):
            kids = [self._build(ch) for ch in self._children(node["id"])]
            return {"type": "folder", "name": node.get("title") or "Untitled",
                    "children": [k for k in kids if k]}
        return None  # easels, split views, notes: not supported in Zen

    def spaces(self):
        """[{name, icon, tree}] in Arc's order."""
        result, unnamed = [], 1
        for sp in self._main.get("spaces", []):
            if not isinstance(sp, dict):
                continue
            name = sp.get("title")
            if not name:
                name, unnamed = f"Space {unnamed}", unnamed + 1
            ids = sp.get("containerIDs") or sp.get("newContainerIDs") or []
            pinned_root = self._pairs(ids).get("pinned")
            tree = []
            if pinned_root:
                for ch in self._children(pinned_root):
                    node = self._build(ch)
                    if node:
                        tree.append(node)
            icon = None
            custom = sp.get("customInfo")
            if isinstance(custom, dict):
                icon = (custom.get("iconType") or {}).get("emoji_v2")
            result.append({"name": name, "icon": icon, "tree": tree})
        return result

    def favorites(self):
        favs, seen = [], set()
        for cid in self._main.get("topAppsContainerIDs", []):
            if not isinstance(cid, str):
                continue
            for it in self._children(cid):
                tab = (it.get("data") or {}).get("tab")
                if not tab:
                    continue
                url = tab.get("savedURL") or ""
                if url and not url.startswith("arc://") and url not in seen:
                    seen.add(url)
                    favs.append({"url": url, "title": it.get("title") or tab.get("savedTitle") or url})
        return favs


def count_tree(nodes):
    folders = tabs = 0
    for n in nodes:
        if n["type"] == "folder":
            folders += 1
            f, t = count_tree(n["children"])
            folders, tabs = folders + f, tabs + t
        else:
            tabs += 1
    return folders, tabs


def find_arc_sidebar():
    """Arc's own file, or the newest copy saved with --save-arc."""
    for d in arc_dir_candidates():
        p = d / "StorableSidebar.json"
        if p.exists():
            return p, False
    saved = sorted(documents_dir().glob(SAVE_DIR_PREFIX + "*/StorableSidebar.json"))
    if saved:
        return saved[-1], True
    return None, False


# ─────────────────────────────────────────────────────────────────────────────
# Zen: profiles and the session store
# ─────────────────────────────────────────────────────────────────────────────
class ZenProfile:
    def __init__(self, path, install_default=False):
        self.path = Path(path)
        self.install_default = install_default
        self.session = self.path / SESSION_FILE

    @property
    def name(self):
        return self.path.name

    @property
    def has_session(self):
        return self.session.exists()

    @property
    def modified(self):
        return self.session.stat().st_mtime if self.has_session else 0

    def space_names(self):
        try:
            return [s.get("name") for s in mozlz4_read(self.session).get("spaces", [])]
        except Exception:
            return None

    def is_running(self):
        """True if a running Zen holds this profile's lock."""
        if IS_WINDOWS:
            lock = self.path / "parent.lock"
            if not lock.exists():
                return False
            try:
                with open(lock, "a"):
                    return False
            except PermissionError:
                return True
            except OSError:
                return False
        lock = self.path / ".parentlock"
        if not lock.exists():
            return False
        try:
            import fcntl
            fd = os.open(lock, os.O_RDWR)
        except (ImportError, OSError):
            return False
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.lockf(fd, fcntl.LOCK_UN)
            return False
        except OSError as e:
            return e.errno in (errno.EACCES, errno.EAGAIN)
        finally:
            os.close(fd)

    def backups(self):
        return sorted(self.path.glob(SESSION_FILE + BACKUP_TAG + "*.bak"))


def zen_process_running():
    try:
        if IS_WINDOWS:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq zen.exe", "/NH"],
                                 capture_output=True, text=True)
            return "zen.exe" in out.stdout.lower()
        out = subprocess.run(["pgrep", "-f", "/Contents/MacOS/zen"],
                             capture_output=True, text=True)
        return out.returncode == 0 and bool(out.stdout.strip())
    except (FileNotFoundError, OSError):
        return False


def find_zen_profiles(base=None):
    """Every Zen profile folder; the ones an install uses by default are flagged."""
    base = base or zen_dir()
    defaults = set()
    ini = base / "installs.ini"
    if ini.exists():
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(ini, encoding="utf-8")
            for sec in cp.sections():
                d = cp.get(sec, "Default", fallback=None)
                if d:
                    p = Path(d) if os.path.isabs(d) else base / d
                    defaults.add(str(p.resolve()))
        except configparser.Error:
            pass
    paths = set()
    for parent in (base / "Profiles", base):
        if parent.is_dir():
            paths.update(p for p in parent.iterdir()
                         if p.is_dir() and (parent.name == "Profiles" or (p / SESSION_FILE).exists()))
    for d in defaults:
        if Path(d).is_dir():
            paths.add(Path(d))
    profiles = {str(p.resolve()): ZenProfile(p, str(p.resolve()) in defaults) for p in paths}
    return sorted(profiles.values(), key=lambda p: p.name)


def recommend_profile(profiles):
    """Index of the most likely profile: an install's default, most recently saved."""
    usable = [i for i, p in enumerate(profiles) if p.has_session]
    if not usable:
        return None
    return max(usable, key=lambda i: (profiles[i].install_default, profiles[i].modified))


class SchemaProblem(Exception):
    pass


def check_schema(data):
    """Return a list of warnings; raise SchemaProblem if writing would be unsafe."""
    if not isinstance(data, dict):
        raise SchemaProblem("the session isn't a JSON object")
    for key in ("tabs", "spaces", "folders", "groups"):
        if key in data and not isinstance(data[key], list):
            raise SchemaProblem(f"'{key}' isn't a list")
    for t in data.get("tabs", []):
        if not isinstance(t, dict) or not isinstance(t.get("entries", []), list):
            raise SchemaProblem("a tab has an unexpected shape")
    for s in data.get("spaces", []):
        if not isinstance(s, dict) or "uuid" not in s:
            raise SchemaProblem("a Space has no id")
    folders = data.get("folders", [])
    groups = data.get("groups", [])
    for f in folders:
        if not isinstance(f, dict) or not REQUIRED_FOLDER_KEYS <= set(f):
            raise SchemaProblem("a folder is missing its id or Space")
    if {f["id"] for f in folders} != {g.get("id") for g in groups if isinstance(g, dict)}:
        raise SchemaProblem("folders and groups don't match up")
    warnings = []
    new_folder_keys = set().union(*[set(f) for f in folders]) - KNOWN_FOLDER_KEYS if folders else set()
    if new_folder_keys:
        warnings.append("Zen folders have fields this tool doesn't know yet: "
                        + ", ".join(sorted(new_folder_keys)))
    new_space_keys = set().union(*[set(s) for s in data.get("spaces", [])]) - KNOWN_SPACE_KEYS \
        if data.get("spaces") else set()
    if new_space_keys:
        warnings.append("Zen Spaces have fields this tool doesn't know yet: "
                        + ", ".join(sorted(new_space_keys)))
    return warnings


class ZenSession:
    """Builds Spaces, folders and pins the way Zen itself stores them:
    every folder starts with an empty placeholder tab, lists its (and its
    sub-folders') placeholders in emptyTabIds, and records what comes
    before it in prevSiblingInfo."""

    def __init__(self, profile):
        self.profile = profile
        self.data = mozlz4_read(profile.session)
        for key in ("tabs", "folders", "groups", "spaces"):
            self.data.setdefault(key, [])
        self._ms = int(time.time() * 1000)
        self._ctr = 0

    def _folder_id(self):
        self._ctr += 1
        return f"{self._ms}-{self._ctr}"

    def _sync_id(self):
        return f"{self._ms}-{uuid.uuid4()}"

    def spaces(self):
        return self.data["spaces"]

    def find_space(self, name):
        for s in self.spaces():
            if s.get("name") == name:
                return s["uuid"]
        return None

    def container_of(self, wid):
        for s in self.spaces():
            if s.get("uuid") == wid:
                return s.get("containerTabId") or 0
        return 0

    def ensure_space(self, name, icon=None):
        existing = self.find_space(name)
        if existing:
            return existing, False
        pos = max([s.get("position", 0) or 0 for s in self.spaces()], default=0) + 1000
        wid = "{" + str(uuid.uuid4()) + "}"
        self.spaces().append({
            "uuid": wid, "name": name, "icon": icon or "🌐",
            "containerTabId": 0, "position": pos,
            "theme": None, "hasCollapsedPinnedTabs": False,
        })
        return wid, True

    def clear_space(self, wid):
        """Remove a Space's pinned tabs and folders. Open (unpinned) tabs stay."""
        drop = {f["id"] for f in self.data["folders"] if f.get("workspaceId") == wid}
        self.data["folders"] = [f for f in self.data["folders"] if f["id"] not in drop]
        self.data["groups"] = [g for g in self.data["groups"] if g.get("id") not in drop]
        self.data["tabs"] = [
            t for t in self.data["tabs"]
            if t.get("groupId") not in drop
            and not (t.get("zenWorkspace") == wid and t.get("pinned"))]

    def _tab(self, url, title, wid, folder_id=None, essential=False, empty=False):
        sync = self._sync_id()
        entry = {"url": url, "triggeringPrincipal_base64": "{\"3\":{}}"}
        if title and not empty:
            entry["title"] = title
        tab = {
            "entries": [entry],
            "lastAccessed": int(time.time() * 1000),
            "pinned": True, "hidden": False,
            "zenWorkspace": wid, "zenSyncId": sync, "zenEssential": essential,
            "zenDefaultUserContextId": None, "zenPinnedIcon": None,
            "zenIsEmpty": empty, "zenHasStaticIcon": False, "zenGlanceId": None,
            "zenIsGlance": False,
            "_zenPinnedInitialState": {"entry": dict(entry), "image": None},
            "zenLiveFolderItemId": None, "searchMode": None,
            "userContextId": 0 if wid is None else self.container_of(wid),
            "attributes": {}, "index": 1, "userTypedValue": "", "userTypedClear": 0,
            "image": None,
        }
        if folder_id:
            tab["groupId"] = folder_id
        self.data["tabs"].append(tab)
        return sync

    def _folder(self, name, wid, parent_id, prev):
        fid = self._folder_id()
        folder = {
            "pinned": True, "splitViewGroup": False, "id": fid, "name": name,
            "collapsed": True, "saveOnWindowClose": True, "parentId": parent_id,
            "prevSiblingInfo": prev, "emptyTabIds": [], "userIcon": "",
            "workspaceId": wid,
        }
        self.data["folders"].append(folder)
        self.data["groups"].append({
            "pinned": True, "splitView": False, "id": fid, "name": name,
            "color": "zen-workspace-color", "collapsed": True,
            "saveOnWindowClose": True,
        })
        return folder

    def add_tree(self, nodes, wid, parent=None, first_prev=None):
        """Add nodes in order. Returns the placeholder tab ids created inside."""
        prev = first_prev or {"type": "start", "id": None}
        placeholders = []
        for n in nodes:
            if n["type"] == "folder":
                folder = self._folder(n["name"], wid, parent, prev)
                own = self._tab("about:blank", None, wid, folder_id=folder["id"], empty=True)
                inner = self.add_tree(n["children"], wid, parent=folder["id"],
                                      first_prev={"type": "tab", "id": own})
                folder["emptyTabIds"] = [own] + inner
                placeholders += [own] + inner
                prev = {"type": "group", "id": folder["id"]}
            else:
                sync = self._tab(n["url"], n["title"], wid, folder_id=parent)
                prev = {"type": "tab", "id": sync}
        return placeholders

    def set_global_essentials(self, favorites):
        self.data["tabs"] = [t for t in self.data["tabs"]
                             if not (t.get("zenEssential") and t.get("zenWorkspace") is None)]
        for f in favorites:
            self._tab(f["url"], f["title"], None, essential=True)

    def save(self):
        mozlz4_write(self.profile.session, self.data)


def make_backup(profile):
    # e.g. zen-sessions.jsonlz4.arc2zen-20261022-231500-01.bak (sorts by time,
    # and never overwrites an earlier backup)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    n = 1
    dst = profile.path / f"{SESSION_FILE}{BACKUP_TAG}{stamp}-{n:02d}.bak"
    while dst.exists():
        n += 1
        dst = profile.path / f"{SESSION_FILE}{BACKUP_TAG}{stamp}-{n:02d}.bak"
    shutil.copy2(profile.session, dst)
    return dst


def enable_global_essentials(profile):
    userjs = profile.path / "user.js"
    text = userjs.read_text(encoding="utf-8") if userjs.exists() else ""
    if "zen.workspaces.separate-essentials" in text:
        return False
    with open(userjs, "a", encoding="utf-8") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        f.write(ESSENTIALS_PREF + "\n")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Bookmarks export (no Zen changes at all)
# ─────────────────────────────────────────────────────────────────────────────
def bookmarks_html(spaces):
    lines = ["<!DOCTYPE NETSCAPE-Bookmark-file-1>",
             '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
             "<TITLE>Bookmarks</TITLE>", "<H1>Bookmarks</H1>", "<DL><p>"]

    def walk(nodes, depth):
        pad = "\t" * depth
        for n in nodes:
            if n["type"] == "folder":
                lines.append(f"{pad}<DT><H3>{html.escape(n['name'])}</H3>")
                lines.append(f"{pad}<DL><p>")
                walk(n["children"], depth + 1)
                lines.append(f"{pad}</DL><p>")
            else:
                lines.append(f'{pad}<DT><A HREF="{html.escape(n["url"], quote=True)}">'
                             f'{html.escape(n["title"])}</A>')

    for sp in spaces:
        lines.append(f"\t<DT><H3>{html.escape(sp['name'])}</H3>")
        lines.append("\t<DL><p>")
        walk(sp["tree"], 2)
        lines.append("\t</DL><p>")
    lines.append("</DL><p>")
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# History (read-only). Arc's History is a plain SQLite file — no passwords here.
# We only ever read it, and we never touch Arc's Login Data / password store.
# ─────────────────────────────────────────────────────────────────────────────
def _chrome_time(microseconds):
    """Chrome timestamps are microseconds since 1601-01-01. Return a date string."""
    if not microseconds:
        return ""
    unix = microseconds / 1_000_000 - 11_644_473_600
    if unix <= 0:
        return ""
    try:
        return datetime.datetime.fromtimestamp(unix).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def read_arc_history(history_file, limit=0):
    """Read Arc's browsing history. Returns [(url, title, visits, last_visit)].
    Opens the file read-only and immutable, so Arc's data is never modified."""
    import sqlite3
    uri = f"file:{Path(history_file).as_uri()[7:]}?mode=ro&immutable=1"
    rows = []
    con = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        q = ("SELECT url, title, visit_count, last_visit_time FROM urls "
             "WHERE hidden = 0 ORDER BY last_visit_time DESC")
        if limit:
            q += f" LIMIT {int(limit)}"
        for url, title, visits, last in con.execute(q):
            if url and url.startswith(("http://", "https://")):
                rows.append((url, title or url, visits or 0, _chrome_time(last)))
    finally:
        con.close()
    return rows


def history_html(rows):
    lines = ["<!DOCTYPE NETSCAPE-Bookmark-file-1>",
             '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
             "<TITLE>Arc History</TITLE>", "<H1>Arc History</H1>", "<DL><p>"]
    for url, title, _visits, last in rows:
        add = f' ADD_DATE="{last}"' if last else ""
        lines.append(f'    <DT><A HREF="{html.escape(url, quote=True)}"{add}>{html.escape(title)}</A>')
    lines.append("</DL><p>")
    return "\n".join(lines) + "\n"


def history_csv(rows):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["url", "title", "visit_count", "last_visit"])
    for url, title, visits, last in rows:
        w.writerow([url, title, visits, last])
    return buf.getvalue()


def read_arc_extensions(user_data):
    """Best-effort list of installed Arc extension names (from each manifest).
    Reads only extension manifests; ignores everything it can't parse."""
    names = []
    if not user_data:
        return names
    for mani in Path(user_data).glob("*/Extensions/*/*/manifest.json"):
        try:
            m = json.loads(mani.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = m.get("name", "")
        if isinstance(name, str) and name and not name.startswith("__MSG_"):
            names.append(name)
    seen, out = set(), []
    for n in sorted(names, key=str.lower):
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────
def load_arc(path_arg, quiet=False):
    if path_arg:
        path, from_save = Path(path_arg).expanduser(), False
    else:
        path, from_save = find_arc_sidebar()
    if not path or not path.exists():
        fail("Arc's sidebar file wasn't found.",
             "Is Arc installed on this computer? If you saved it earlier, point to the copy:\n"
             f"  {RUN} --arc \"path/to/StorableSidebar.json\"")
    try:
        arc = ArcSidebar(path)
    except (ValueError, json.JSONDecodeError) as e:
        fail(f"Couldn't read {path}: {e}")
    if from_save and not quiet:
        ok(f"Using your saved copy: {path.parent.name}")
    return arc


def print_arc(spaces, favorites):
    for sp in spaces:
        f, t = count_tree(sp["tree"])
        icon = f"{sp['icon']} " if sp["icon"] else ""
        info(f"{icon}{bold(sp['name'])}: {f} folders, {t} pinned tabs")
    info(f"Favorites: {len(favorites)}")


def pick_profile(args, ask):
    if args.profile:
        p = Path(args.profile).expanduser()
        if not (p / SESSION_FILE).exists() and (zen_dir() / "Profiles" / args.profile).exists():
            p = zen_dir() / "Profiles" / args.profile
        prof = ZenProfile(p)
        if not prof.has_session:
            fail(f"No {SESSION_FILE} in {p}",
                 "Open Zen once with this profile, quit it, and try again.")
        ok(f"Using the profile you chose: {prof.name}")
        return prof

    profiles = find_zen_profiles()
    usable = [p for p in profiles if p.has_session]
    if not usable:
        fail("No Zen profile with saved tabs was found.",
             "Install Zen, open it once, quit it completely, then run this again.")
    rec = recommend_profile(profiles)

    if len(usable) == 1:
        ok(f"Found one Zen profile: {usable[0].name}")
        return usable[0]

    warn(f"Found {len(profiles)} Zen profiles. Only one is the Zen you use; "
         "writing to the wrong one changes nothing you can see.")
    for i, p in enumerate(profiles):
        if not p.has_session:
            info(dim(f"  {i + 1}. {p.name} — empty, skipped"))
            continue
        names = p.space_names()
        when = datetime.datetime.fromtimestamp(p.modified).strftime("%d %b %Y %H:%M")
        tags = []
        if p.install_default:
            tags.append("default for a Zen install")
        if i == rec:
            tags.append(green("recommended"))
        spaces_txt = ", ".join(n or "?" for n in names) if names else "no Spaces"
        info(f"  {i + 1}. {bold(p.name)}  {dim('saved ' + when)}"
             + (f"  ({'; '.join(tags)})" if tags else ""))
        info(f"     Spaces: {spaces_txt}")
    info(dim("  Not sure? Open Zen, create a Space with an unusual name, quit Zen, run this"
             " again, and pick the profile that shows it."))
    while True:
        idx = ask.choose("Which profile should I use?", len(profiles), rec)
        if profiles[idx].has_session:
            ok(f"Using {profiles[idx].name}")
            return profiles[idx]
        warn("That one is empty; pick another.")


def require_zen_closed(profile):
    if profile.is_running() or zen_process_running():
        how = "close every Zen window and wait a few seconds" if IS_WINDOWS else "quit it completely with Cmd+Q"
        fail(f"Zen is open. Please {how}, then run this again.",
             "Zen saves its tabs when it quits, so it would overwrite the migration.")
    ok("Zen is closed")


def cmd_bookmarks(args):
    arc = load_arc(args.arc, quiet=True)
    spaces = arc.spaces()
    out = Path(args.bookmarks).expanduser()
    out.write_text(bookmarks_html(spaces), encoding="utf-8")
    total = sum(count_tree(s["tree"])[1] for s in spaces)
    print(f"{green('✓')} Wrote {total} links from {len(spaces)} Spaces to {out}")
    print("  Import in Zen: Library (Cmd/Ctrl+Shift+O) → Import and Backup → Import Bookmarks from HTML…")


def cmd_save_arc(args):
    print(bold(f"arc2zen {VERSION}") + dim(" — save a copy of your Arc sidebar"))
    step(1, "Reading Arc")
    src = Path(args.arc).expanduser() if args.arc else None
    if not src:
        for d in arc_dir_candidates():
            if (d / "StorableSidebar.json").exists():
                src = d / "StorableSidebar.json"
                break
    if not src or not src.exists():
        fail("Arc's sidebar file wasn't found on this computer.",
             "Is Arc installed? This needs to run on the computer where you use Arc.")
    arc = load_arc(str(src), quiet=True)
    spaces = arc.spaces()
    print_arc(spaces, arc.favorites())

    step(2, "Saving")
    stamp = datetime.date.today().isoformat()
    dest = documents_dir() / f"{SAVE_DIR_PREFIX}{stamp}"
    n = 2
    while dest.exists():
        dest = documents_dir() / f"{SAVE_DIR_PREFIX}{stamp} ({n})"
        n += 1
    dest.mkdir(parents=True)
    shutil.copy2(src, dest / "StorableSidebar.json")
    older = sorted(src.parent.glob("StorableSidebar.*.json"))
    if older:
        (dest / "older copies").mkdir()
        for f in older:
            shutil.copy2(f, dest / "older copies" / f.name)
    (dest / "Arc pinned tabs.html").write_text(bookmarks_html(spaces), encoding="utf-8")
    (dest / "README.txt").write_text(
        "Your Arc sidebar, saved by arc2zen on " + stamp + ".\n\n"
        "StorableSidebar.json   Arc's own file with every Space, folder and pinned tab.\n"
        "Arc pinned tabs.html   The same links as a bookmarks file any browser can import.\n"
        "older copies           Earlier versions Arc kept automatically.\n\n"
        "To rebuild your sidebar in Zen later, run arc2zen; it finds this folder by itself.\n"
        "Guide: https://arctozen.pages.dev\n", encoding="utf-8")
    ok(f"Saved to {dest}")
    ok(f"{len(older)} older copies kept" if older else "Sidebar saved")
    ok("Bookmarks file: Arc pinned tabs.html")
    print(f"\n{green(bold('Done.'))} Your Arc sidebar is safe, even after Arc shuts down.")
    print(dim(f"When you're ready to switch, run:  {RUN}"))


def _find_arc_history():
    ud = arc_user_data()
    if not ud:
        return None
    for name in ("Default", ""):
        h = (ud / name / "History") if name else (ud / "History")
        if h.exists():
            return h
    found = sorted(ud.glob("*/History"))
    return found[0] if found else None


def cmd_export_history(args):
    print(bold(f"arc2zen {VERSION}") + dim(" — export your Arc history"))
    step(1, "Reading Arc history")
    hist = Path(args.arc_history).expanduser() if args.arc_history else _find_arc_history()
    if not hist or not hist.exists():
        fail("Couldn't find Arc's history file.",
             "It lives in Arc's 'User Data' folder. If Arc is on another Mac, copy that\n"
             "  folder over, or point to the file:  " + RUN + " --export-history --arc-history PATH")
    try:
        rows = read_arc_history(hist, limit=args.limit or 0)
    except Exception as e:
        fail(f"Couldn't read the history file: {e}",
             "Make sure Arc is fully quit, then try again.")
    info(f"{len(rows)} pages found")

    step(2, "Writing")
    out = Path(args.out).expanduser() if args.out else documents_dir() / f"Arc history {datetime.date.today().isoformat()}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "history.html").write_text(history_html(rows), encoding="utf-8")
    (out / "history.csv").write_text(history_csv(rows), encoding="utf-8")
    ok(f"history.html and history.csv written to {out}")
    print(f"\n{green(bold('Done.'))}")
    print(dim("Open history.html to browse or search your old pages. Zen has no direct\n"
              "history import, so this keeps your history readable even after Arc is gone."))


def cmd_report(args):
    print(bold(f"arc2zen {VERSION}") + dim(" — what moves, and what to do by hand"))
    step(1, "Looking at Arc")
    arc = load_arc(args.arc, quiet=True)
    spaces = arc.spaces()
    favorites = arc.favorites()
    ud = arc_user_data()
    hist = _find_arc_history()
    history_count = 0
    if hist:
        try:
            history_count = len(read_arc_history(hist))
        except Exception:
            history_count = 0
    extensions = read_arc_extensions(ud)
    total_folders = sum(count_tree(s["tree"])[0] for s in spaces)
    total_tabs = sum(count_tree(s["tree"])[1] for s in spaces)

    step(2, "Writing the report")
    out = Path(args.out).expanduser() if args.out else documents_dir() / f"Arc to Zen report {datetime.date.today().isoformat()}.html"
    out.write_text(report_html(spaces, favorites, total_folders, total_tabs,
                               history_count, extensions), encoding="utf-8")
    ok(f"Report written to {out}")
    info(f"{len(spaces)} Spaces, {total_folders} folders, {total_tabs} pinned tabs, "
         f"{len(favorites)} Favorites, {history_count} history entries, {len(extensions)} extensions")
    print(f"\n{green(bold('Done.'))} Open the report to see what moves automatically and what to do by hand.")


def report_html(spaces, favorites, folders, tabs, history_count, extensions):
    def li(name, note):
        return f"<li><b>{html.escape(name)}</b> — {html.escape(note)}</li>"
    ext_rows = ""
    for name in extensions:
        hint = EXTENSION_HINTS.get(name.lower(), "search addons.mozilla.org for it or an alternative")
        ext_rows += f"<tr><td>{html.escape(name)}</td><td>{html.escape(hint)}</td></tr>"
    if not extensions:
        ext_rows = '<tr><td colspan="2">No extensions found (or Arc wasn\'t readable). Check Arc → Extensions.</td></tr>'
    moved = "".join(li(*m) for m in [
        (f"{len(spaces)} Spaces, {folders} folders, {tabs} pinned tabs", "rebuilt in Zen by arc2zen"),
        (f"{len(favorites)} Favorites", "added as Zen Essentials by arc2zen"),
    ])
    byhand = "".join(li(*m) for m in [
        ("Passwords", "export from Arc's settings to a file, import it in Zen, then delete the file"),
        ("History" + (f" ({history_count} pages)" if history_count else ""),
         "run  python3 arc2zen.py --export-history  to keep it readable; Zen has no direct import"),
        ("Extensions", "reinstall from addons.mozilla.org — see the list below"),
    ])
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arc to Zen — your migration report</title>
<style>
 :root{{color-scheme:light dark}}
 body{{font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:720px;margin:0 auto;padding:32px 20px;
   background:#131211;color:#ece9e2}}
 @media(prefers-color-scheme:light){{body{{background:#f6f5f2;color:#1b1a18}}}}
 h1{{font-size:30px;letter-spacing:-.02em}} h2{{font-size:20px;margin-top:32px}}
 .red{{color:#e8462d}} ul{{padding-left:20px}} li{{margin:6px 0}}
 table{{width:100%;border-collapse:collapse;margin-top:8px}}
 td{{padding:8px 10px;border-top:1px solid #8884;vertical-align:top}}
 .check li{{list-style:none}} .check li::before{{content:"☐ ";color:#e8462d}}
 small{{opacity:.7}}
</style></head><body>
<h1>Your move from Arc to <span class="red">Zen</span></h1>
<p><small>Made by arc2zen on {datetime.date.today().isoformat()}. Arc shuts down December 22, 2026.</small></p>
<h2>Moved automatically</h2><ul>{moved}</ul>
<h2>To do by hand</h2><ul>{byhand}</ul>
<h2>Your extensions</h2>
<table><tr><td><b>In Arc</b></td><td><b>In Zen (Firefox add-ons)</b></td></tr>{ext_rows}</table>
<h2>Before December 22</h2>
<ul class="check">
 <li>Run arc2zen to move your Spaces, folders and pinned tabs</li>
 <li>Export your passwords from Arc and import them in Zen, then delete the export file</li>
 <li>Export your history with --export-history if you want to keep it</li>
 <li>Reinstall the extensions above from addons.mozilla.org</li>
 <li>Run  python3 arc2zen.py --save-arc  to keep a copy of your Arc sidebar just in case</li>
</ul>
<p><small>Guide: arctozen.pages.dev · Passwords are never read or moved by this tool — you move them with the browsers' own export and import.</small></p>
</body></html>"""


def cmd_list(args, ask):
    step(1, "Arc")
    arc = load_arc(args.arc)
    print_arc(arc.spaces(), arc.favorites())
    step(2, "Zen profiles")
    profiles = find_zen_profiles()
    if not profiles:
        info("No Zen profiles found.")
    rec = recommend_profile(profiles)
    for i, p in enumerate(profiles):
        if not p.has_session:
            info(dim(f"{p.name} — no saved tabs"))
            continue
        names = p.space_names() or []
        flag = green("  ← recommended") if i == rec else ""
        info(f"{bold(p.name)}{flag}")
        info(f"   Spaces: {', '.join(n or '?' for n in names) or 'none'}"
             f"   backups: {len(p.backups())}")
    print(dim("\nNothing was changed."))


def cmd_undo(args, ask):
    step(1, "Finding the Zen profile arc2zen changed")
    with_backups = [p for p in find_zen_profiles() if p.backups()] if not args.profile else []
    if len(with_backups) == 1:
        prof = with_backups[0]
        ok(f"Using {prof.name}")
    else:
        prof = pick_profile(args, ask)
    backups = prof.backups()
    if not backups:
        fail("No arc2zen backups in this profile, so there's nothing to undo.",
             "If you used another tool, restore its .bak file or your own backup instead.")
    step(2, "Check Zen is closed")
    require_zen_closed(prof)
    step(3, "Restore")
    first, latest = backups[0], backups[-1]
    target = first
    if first == latest:
        info(f"Backup: {first.name} (how Zen looked before arc2zen ran)")
    else:
        info(f"1. {first.name}  {dim('— oldest: Zen before arc2zen ever ran')}")
        info(f"2. {latest.name}  {dim('— latest: Zen just before the last run')}")
        target = (first, latest)[ask.choose("Restore which one?", 2, 0)]
    if not ask.yes_no(f"Replace the current Zen tabs with {target.name}?", True):
        print("Cancelled. Nothing changed.")
        return
    safety = make_backup(prof)
    shutil.copy2(target, prof.session)
    ok(f"Restored {target.name}")
    ok(f"The state you just replaced was saved as {safety.name}")
    print(f"\n{green('Done.')} Open Zen to check.")


def verify_written(prof, expected_spaces, expected_folders, expected_tabs):
    """Re-read the saved session and check every Space landed as planned."""
    data = mozlz4_read(prof.session)
    check_schema(data)
    for name, wid in expected_spaces.items():
        folders = sum(1 for f in data["folders"] if f.get("workspaceId") == wid)
        tabs = sum(1 for t in data["tabs"] if t.get("zenWorkspace") == wid
                   and t.get("pinned") and not t.get("zenIsEmpty") and not t.get("zenEssential"))
        if folders != expected_folders[name] or tabs != expected_tabs[name]:
            raise SchemaProblem(f"{name}: expected {expected_folders[name]} folders and "
                                f"{expected_tabs[name]} tabs, found {folders} and {tabs}")


def cmd_migrate(args, ask):
    print(bold(f"arc2zen {VERSION}") + dim(" — move your Arc sidebar into Zen"))

    step(1, "Reading Arc")
    arc = load_arc(args.arc)
    spaces = arc.spaces()
    favorites = arc.favorites()
    if not spaces:
        fail("Arc's sidebar has no Spaces to move.")
    print_arc(spaces, favorites)

    if args.spaces:
        wanted = [s.strip() for s in args.spaces.split(",") if s.strip()]
        missing = [w for w in wanted if w not in {s["name"] for s in spaces}]
        if missing:
            fail(f"Arc has no Space named {', '.join(missing)}.",
                 f"Arc Spaces: {', '.join(s['name'] for s in spaces)}")
        spaces = [s for s in spaces if s["name"] in wanted]

    step(2, "Finding your Zen profile")
    prof = pick_profile(args, ask)

    step(3, "Checking Zen is closed")
    require_zen_closed(prof)

    try:
        session = ZenSession(prof)
        warnings = check_schema(session.data)
    except SchemaProblem as e:
        fail(f"Zen's session file isn't in a format this version knows ({e}).",
             "Nothing was changed. Zen may have updated; check for a newer arc2zen:\n"
             "  https://github.com/Glebelov96/arc-to-zen-guide")
    except Exception as e:
        fail(f"Couldn't read Zen's session file: {e}", "Nothing was changed.")
    ok("Zen's session format looks familiar")
    if warnings:
        for w in warnings:
            warn(w)
        warn("Zen may have changed how it stores folders. The migration might not look right.")
        if not args.force and (args.yes or not ask.yes_no("Continue anyway? A backup is made first.", False)):
            fail("Stopped. Nothing was changed.",
                 f"To try anyway: {RUN} --force   (you can always undo with --undo)")

    step(4, "Plan")
    replacing = [s["name"] for s in spaces if session.find_space(s["name"])]
    for s in spaces:
        f, t = count_tree(s["tree"])
        action = yellow("replace pinned tabs in") if s["name"] in replacing else green("create")
        info(f"{action} Space {bold(s['name'])} — {f} folders, {t} pinned tabs")
    use_favs = bool(favorites) and not args.no_favorites
    if use_favs and not args.yes:
        use_favs = ask.yes_no(f"Also add your {len(favorites)} Arc Favorites as Zen Essentials?", True)
    if use_favs:
        info(f"{green('add')} {len(favorites)} Favorites as Essentials")
    if replacing:
        warn(f"Zen already has {', '.join(replacing)}: their pinned tabs and folders "
             "will be replaced by Arc's. Your open (unpinned) tabs stay.")

    if args.dry_run:
        print(dim("\nDry run: nothing was changed."))
        return
    if not ask.yes_no("Go ahead?", True):
        print("Cancelled. Nothing changed.")
        return

    step(5, "Migrating")
    backup = make_backup(prof)
    ok(f"Backup saved: {backup.name}")
    expected_spaces, exp_f, exp_t = {}, {}, {}
    for s in spaces:
        wid, _ = session.ensure_space(s["name"], icon=s["icon"])
        session.clear_space(wid)
        session.add_tree(s["tree"], wid)
        f, t = count_tree(s["tree"])
        expected_spaces[s["name"]], exp_f[s["name"]], exp_t[s["name"]] = wid, f, t
        ok(f"{s['name']}: {f} folders, {t} pinned tabs")
    if use_favs:
        session.set_global_essentials(favorites)
        ok(f"{len(favorites)} Essentials")
        if enable_global_essentials(prof):
            ok("Essentials set to show in every Space (like Arc Favorites)")
    session.save()

    try:
        verify_written(prof, expected_spaces, exp_f, exp_t)
    except Exception as e:
        shutil.copy2(backup, prof.session)
        fail(f"The saved result didn't check out ({e}), so Zen was put back as it was.",
             "Please report this: https://github.com/Glebelov96/arc-to-zen-guide/issues")
    ok("Saved and checked")

    print(f"\n{green(bold('Done.'))} Open Zen — your Arc Spaces are in the sidebar.")
    print(dim(f"Something off? Close Zen and run:  {RUN} --undo"))


def main():
    ap = argparse.ArgumentParser(
        prog="arc2zen",
        description="Move your Arc sidebar (Spaces, folders, pinned tabs, Favorites) into Zen Browser.")
    ap.add_argument("--save-arc", action="store_true",
                    help="save a copy of your Arc sidebar to Documents, to migrate later")
    ap.add_argument("--export-history", action="store_true",
                    help="save your Arc browsing history as HTML and CSV (read-only)")
    ap.add_argument("--report", action="store_true",
                    help="write a report of what moves and what to do by hand (passwords, extensions)")
    ap.add_argument("--arc-history", metavar="PATH", help="path to Arc's History file (default: Arc's own)")
    ap.add_argument("--out", metavar="PATH", help="where to write (for --export-history and --report)")
    ap.add_argument("--limit", type=int, metavar="N", help="most-recent N history entries only")
    ap.add_argument("--list", action="store_true", help="show Arc Spaces and Zen profiles; change nothing")
    ap.add_argument("--dry-run", action="store_true", help="show the plan; change nothing")
    ap.add_argument("--undo", action="store_true", help="restore Zen from an arc2zen backup")
    ap.add_argument("--bookmarks", metavar="FILE", help="only export Arc pinned tabs as a bookmarks HTML file")
    ap.add_argument("--arc", metavar="PATH", help="path to StorableSidebar.json (default: Arc's own or a saved copy)")
    ap.add_argument("--profile", metavar="PATH", help="Zen profile folder or its name in Profiles/")
    ap.add_argument("--spaces", metavar="A,B", help="only these Arc Spaces (comma-separated)")
    ap.add_argument("--no-favorites", action="store_true", help="don't turn Arc Favorites into Essentials")
    ap.add_argument("--force", action="store_true", help="continue even if Zen's format has new fields")
    ap.add_argument("-y", "--yes", action="store_true", help="don't ask questions; use the recommended answers")
    ap.add_argument("--version", action="version", version=f"arc2zen {VERSION}")
    args = ap.parse_args()

    ask = Prompt(assume_yes=args.yes)
    try:
        if args.save_arc:
            cmd_save_arc(args)
        elif args.export_history:
            cmd_export_history(args)
        elif args.report:
            cmd_report(args)
        elif args.bookmarks:
            cmd_bookmarks(args)
        elif args.list:
            cmd_list(args, ask)
        elif args.undo:
            cmd_undo(args, ask)
        else:
            cmd_migrate(args, ask)
    except KeyboardInterrupt:
        print("\nStopped. Nothing was changed after the last ✓.")
        sys.exit(130)


if __name__ == "__main__":
    main()
