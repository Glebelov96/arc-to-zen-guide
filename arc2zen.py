#!/usr/bin/env python3
"""
arc2zen — move your Arc sidebar (Spaces, nested folders, pinned tabs,
Favorites) into Zen Browser on macOS, in one command.

    python3 arc2zen.py              # guided migration
    python3 arc2zen.py --list       # show what was found, change nothing
    python3 arc2zen.py --undo       # put Zen back the way it was
    python3 arc2zen.py --bookmarks arc.html   # just export a bookmarks file

What it does, in order:
  1. Reads Arc's sidebar file (~/Library/Application Support/Arc/StorableSidebar.json).
  2. Finds every Zen profile and picks the one Zen actually uses
     (asks you if it isn't obvious) — the step most guides get wrong.
  3. Refuses to continue while Zen is running.
  4. Makes a timestamped backup that later runs never overwrite.
  5. Rebuilds each Arc Space as a Zen Space with the same folders and pins.

No installs needed: works with the Python 3 that ships with macOS.

Guide and source: https://github.com/Glebelov96/arc-to-zen-guide

---------------------------------------------------------------------------
The Zen session builders below are adapted from arc-to-zen-migration
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

VERSION = "1.0.0"

ARC_DIR = Path.home() / "Library" / "Application Support" / "Arc"
ZEN_DIR = Path.home() / "Library" / "Application Support" / "zen"
SESSION_FILE = "zen-sessions.jsonlz4"
BACKUP_TAG = ".arc2zen-"          # zen-sessions.jsonlz4.arc2zen-20261022-231500.bak
MOZLZ4_MAGIC = b"mozLz40\x00"
ESSENTIALS_PREF = 'user_pref("zen.workspaces.separate-essentials", false);'


# ─────────────────────────────────────────────────────────────────────────────
# Terminal output
# ─────────────────────────────────────────────────────────────────────────────
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
        out = subprocess.run(["pgrep", "-f", "/Contents/MacOS/zen"],
                             capture_output=True, text=True)
        return out.returncode == 0 and bool(out.stdout.strip())
    except FileNotFoundError:
        return False


def find_zen_profiles(zen_dir=ZEN_DIR):
    """Every Zen profile folder; the ones an install uses by default are flagged."""
    defaults = set()
    ini = zen_dir / "installs.ini"
    if ini.exists():
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(ini, encoding="utf-8")
            for sec in cp.sections():
                d = cp.get(sec, "Default", fallback=None)
                if d:
                    defaults.add(str((zen_dir / d).resolve()) if not os.path.isabs(d) else str(Path(d).resolve()))
        except configparser.Error:
            pass
    paths = set()
    pdir = zen_dir / "Profiles"
    if pdir.is_dir():
        paths.update(p for p in pdir.iterdir() if p.is_dir())
    for d in defaults:
        if Path(d).is_dir():
            paths.add(Path(d))
    profiles = [ZenProfile(p, str(Path(p).resolve()) in defaults) for p in paths]
    return sorted(profiles, key=lambda p: p.name)


def recommend_profile(profiles):
    """Index of the most likely profile: an install's default, most recently saved."""
    usable = [i for i, p in enumerate(profiles) if p.has_session]
    if not usable:
        return None
    return max(usable, key=lambda i: (profiles[i].install_default, profiles[i].modified))


class ZenSession:
    def __init__(self, profile):
        self.profile = profile
        self.data = mozlz4_read(profile.session)
        for key in ("tabs", "folders", "groups", "spaces"):
            self.data.setdefault(key, [])
        self._base = int(time.time() * 1000)
        self._ctr = 0

    def _id(self):
        self._ctr += 1
        return f"{self._base}-{self._ctr}"

    def spaces(self):
        return self.data["spaces"]

    def find_space(self, name):
        for s in self.spaces():
            if s.get("name") == name:
                return s["uuid"]
        return None

    def ensure_space(self, name, icon=None):
        existing = self.find_space(name)
        if existing:
            return existing, False
        pos = max([s.get("position", 0) for s in self.spaces()], default=0) + 1000
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

    def add_folder(self, name, wid, parent_id=None, collapsed=True):
        fid = self._id()
        self.data["folders"].append({
            "pinned": True, "splitViewGroup": False, "id": fid, "name": name,
            "collapsed": collapsed, "saveOnWindowClose": True, "parentId": parent_id,
            "prevSiblingInfo": None, "emptyTabIds": [], "userIcon": "",
            "workspaceId": wid,
        })
        self.data["groups"].append({
            "pinned": True, "splitView": False, "id": fid, "name": name,
            "color": "zen-workspace-color", "collapsed": collapsed,
            "saveOnWindowClose": True,
        })
        return fid

    def add_tab(self, url, title, wid=None, folder_id=None, essential=False):
        title = title or url
        tab = {
            "entries": [{"url": url, "title": title,
                         "triggeringPrincipal_base64": "{\"3\":{}}"}],
            "lastAccessed": int(time.time() * 1000),
            "pinned": True, "hidden": False,
            "zenWorkspace": wid, "zenSyncId": self._id(), "zenEssential": essential,
            "zenDefaultUserContextId": None, "zenPinnedIcon": None,
            "zenIsEmpty": False, "zenHasStaticIcon": False, "zenGlanceId": None,
            "zenIsGlance": False,
            "_zenPinnedInitialState": {"entry": {"url": url, "title": title}, "image": None},
            "zenLiveFolderItemId": None, "searchMode": None, "userContextId": 0,
            "attributes": {}, "index": 1, "userTypedValue": "", "userTypedClear": 0,
            "image": None,
        }
        if folder_id:
            tab["groupId"] = folder_id
        self.data["tabs"].append(tab)

    def add_tree(self, nodes, wid, parent=None):
        for n in nodes:
            if n["type"] == "folder":
                fid = self.add_folder(n["name"], wid, parent_id=parent)
                self.add_tree(n["children"], wid, parent=fid)
            else:
                self.add_tab(n["url"], n["title"], wid=wid, folder_id=parent)

    def set_global_essentials(self, favorites):
        self.data["tabs"] = [t for t in self.data["tabs"]
                             if not (t.get("zenEssential") and t.get("zenWorkspace") is None)]
        for f in favorites:
            self.add_tab(f["url"], f["title"], wid=None, essential=True)

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
# Commands
# ─────────────────────────────────────────────────────────────────────────────
def load_arc(path_arg):
    path = Path(path_arg).expanduser() if path_arg else ARC_DIR / "StorableSidebar.json"
    if not path.exists():
        fail(f"Arc's sidebar file wasn't found at {path}",
             "Is Arc installed on this Mac? You can also point to a copy with --arc PATH.")
    try:
        arc = ArcSidebar(path)
    except (ValueError, json.JSONDecodeError) as e:
        fail(f"Couldn't read {path}: {e}")
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
        if not (p / SESSION_FILE).exists() and (ZEN_DIR / "Profiles" / args.profile).exists():
            p = ZEN_DIR / "Profiles" / args.profile
        prof = ZenProfile(p)
        if not prof.has_session:
            fail(f"No {SESSION_FILE} in {p}",
                 "Open Zen once with this profile, quit it (Cmd+Q), and try again.")
        ok(f"Using the profile you chose: {prof.name}")
        return prof

    profiles = find_zen_profiles()
    usable = [p for p in profiles if p.has_session]
    if not usable:
        fail("No Zen profile with saved tabs was found.",
             "Install Zen, open it once, quit it with Cmd+Q, then run this again.")
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
    info(dim("  Not sure? Open Zen, rename or create a Space, quit Zen, run this again,"
             " and pick the profile that shows it."))
    while True:
        idx = ask.choose("Which profile should I use?", len(profiles), rec)
        if profiles[idx].has_session:
            ok(f"Using {profiles[idx].name}")
            return profiles[idx]
        warn("That one is empty; pick another.")


def require_zen_closed(profile):
    if profile.is_running() or zen_process_running():
        fail("Zen is open. Quit it completely with Cmd+Q, then run this again.",
             "Zen saves its tabs when it quits, so it would overwrite the migration.")
    ok("Zen is closed")


def cmd_bookmarks(args):
    arc = load_arc(args.arc)
    spaces = arc.spaces()
    out = Path(args.bookmarks).expanduser()
    out.write_text(bookmarks_html(spaces), encoding="utf-8")
    total = sum(count_tree(s["tree"])[1] for s in spaces)
    print(f"{green('✓')} Wrote {total} links from {len(spaces)} Spaces to {out}")
    print("  Import in Zen: Cmd+Shift+O → Import and Backup → Import Bookmarks from HTML…")


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
    except Exception as e:
        fail(f"Couldn't read Zen's session file: {e}",
             "Your Zen version may store tabs differently. Nothing was changed.")

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
    for s in spaces:
        wid, _ = session.ensure_space(s["name"], icon=s["icon"])
        session.clear_space(wid)
        session.add_tree(s["tree"], wid)
        f, t = count_tree(s["tree"])
        ok(f"{s['name']}: {f} folders, {t} pinned tabs")
    if use_favs:
        session.set_global_essentials(favorites)
        ok(f"{len(favorites)} Essentials")
        if enable_global_essentials(prof):
            ok("Essentials set to show in every Space (like Arc Favorites)")
    session.save()
    ok("Saved")

    print(f"\n{green(bold('Done.'))} Open Zen — your Arc Spaces are in the sidebar.")
    print(dim("Something off? Quit Zen and run:  python3 arc2zen.py --undo"))


def main():
    ap = argparse.ArgumentParser(
        prog="arc2zen",
        description="Move your Arc sidebar (Spaces, folders, pinned tabs, Favorites) into Zen Browser.")
    ap.add_argument("--list", action="store_true", help="show Arc Spaces and Zen profiles; change nothing")
    ap.add_argument("--dry-run", action="store_true", help="show the plan; change nothing")
    ap.add_argument("--undo", action="store_true", help="restore Zen from an arc2zen backup")
    ap.add_argument("--bookmarks", metavar="FILE", help="only export Arc pinned tabs as a bookmarks HTML file")
    ap.add_argument("--arc", metavar="PATH", help="path to StorableSidebar.json (default: Arc's own)")
    ap.add_argument("--profile", metavar="PATH", help="Zen profile folder or its name in Profiles/")
    ap.add_argument("--spaces", metavar="A,B", help="only these Arc Spaces (comma-separated)")
    ap.add_argument("--no-favorites", action="store_true", help="don't turn Arc Favorites into Essentials")
    ap.add_argument("-y", "--yes", action="store_true", help="don't ask questions; use the recommended answers")
    ap.add_argument("--version", action="version", version=f"arc2zen {VERSION}")
    args = ap.parse_args()

    ask = Prompt(assume_yes=args.yes)
    try:
        if args.bookmarks:
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
