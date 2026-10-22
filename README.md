# Moving from Arc to Zen without losing your sidebar

**Arc is shutting down on December 22, 2026.** Here's how I moved **2 Spaces, 54 nested folders, 167 pinned links and 26 Favorites** from Arc into [Zen Browser](https://zen-browser.app/) in one go. It also covers the one trap that made my first attempt silently do nothing.

> Tested on macOS, October 2026

## Quick start: one command

`arc2zen.py` does the whole migration for you. It finds your Arc sidebar, picks the Zen profile you actually use, checks that Zen is closed, makes a backup, and rebuilds your Spaces, folders, pinned tabs and Favorites in Zen. Then it reads the result back and restores the backup if anything doesn't match. It needs no extra installs: just Python 3.8+.

> Not technical? The same steps for non-technical people: **[arctozen.pages.dev](https://arctozen.pages.dev)**

### Mac

1. Quit Zen with **Cmd+Q**.
2. Open **Terminal** and run:

```bash
cd ~ && curl -fsSLO https://raw.githubusercontent.com/Glebelov96/arc-to-zen-guide/main/arc2zen.py && python3 arc2zen.py
```

3. Answer its questions (just press Enter for the recommended answer), then open Zen.

If macOS asks to install **Command Line Developer Tools**, click Install and run the command again.

### Windows (experimental)

1. Install Python from [python.org](https://www.python.org/downloads/windows/) if you don't have it, then close every Zen window.
2. Open **PowerShell** and run:

```powershell
cd ~; iwr https://raw.githubusercontent.com/Glebelov96/arc-to-zen-guide/main/arc2zen.py -OutFile arc2zen.py; py arc2zen.py
```

Windows support is tested with automated tests only, not yet on a real Windows PC. Reports are welcome in [Issues](https://github.com/Glebelov96/arc-to-zen-guide/issues).

### Not ready to switch yet? Save your Arc now

Arc stops working on December 22, 2026. This saves a copy of your sidebar to your Documents folder in seconds, so you can migrate later, even after Arc is gone:

```bash
python3 arc2zen.py --save-arc
```

You get an `Arc backup YYYY-MM-DD` folder with Arc's sidebar file, older copies Arc kept, and a bookmarks file any browser can import. When you later run `arc2zen.py` without Arc, it finds that folder by itself.

### No Terminal? Double-click instead (Mac)

Download [`arc2zen.command`](https://raw.githubusercontent.com/Glebelov96/arc-to-zen-guide/main/arc2zen.command), then in Finder **right-click it → Open** (needed once, so macOS trusts it). It downloads the helper and runs the guided migration in a window, no typing. Everything below also works with `./arc2zen.command` in place of `python3 arc2zen.py`.

### The rest of your move: history, passwords, extensions

The migration moves your sidebar. For everything else, run:

```bash
python3 arc2zen.py --report
```

This writes a short HTML report: what moved automatically, and a checklist for the rest, with your installed extensions matched to their Firefox add-ons.

- **History** — `python3 arc2zen.py --export-history` reads Arc's history (read-only) and saves it as HTML and CSV you can keep and search. Zen has no direct history import, so this keeps it readable after Arc is gone.
- **Passwords** — this tool **never reads or moves your passwords.** Export them from Arc's own settings to a file, import that file in Zen, then delete it. The browsers do the encryption; arc2zen stays out of it entirely.
- **Extensions** — the report lists each one with where to find it on addons.mozilla.org.

### All commands

On Windows, use `py` instead of `python3`.

| Command | What it does |
|---|---|
| `python3 arc2zen.py` | Guided migration |
| `python3 arc2zen.py --save-arc` | Save a copy of your Arc sidebar to Documents |
| `python3 arc2zen.py --report` | Write a report: what moves, and what to do by hand |
| `python3 arc2zen.py --export-history` | Save your Arc history as HTML and CSV (read-only) |
| `python3 arc2zen.py --list` | Show your Arc Spaces and Zen profiles, change nothing |
| `python3 arc2zen.py --dry-run` | Show what would happen, change nothing |
| `python3 arc2zen.py --undo` | Restore Zen from a backup (quit Zen first) |
| `python3 arc2zen.py --spaces "Personal,Work"` | Migrate only some Spaces |
| `python3 arc2zen.py --no-favorites` | Don't turn Arc Favorites into Essentials |
| `python3 arc2zen.py --bookmarks arc.html` | Only export a bookmarks file, don't touch Zen |
| `python3 arc2zen.py --profile "NAME"` | Use a specific Zen profile folder |

Compared with running the scripts by hand, it:
- **picks the right Zen profile**, and lists them all with their Spaces when you have more than one (see [the multi-profile trap](#the-multi-profile-trap));
- **refuses to run while Zen is open**;
- **keeps every backup**, each with a timestamp, so `--undo` can always get back to how Zen looked before the first run;
- **leaves your open tabs alone**, replacing only pinned tabs and folders in Spaces with the same name;
- **makes Essentials show in every Space**, like Arc Favorites;
- **writes folders the way Zen itself does**: each folder gets Zen's placeholder tab, its order is recorded, and tabs open in the Space's own container. This is based on a real Zen session from October 2026;
- **stops if Zen's format has changed**, instead of guessing, and **checks its own work** after saving;
- **works without Arc** after December 22, using a copy saved with `--save-arc`.

There's a test suite with no dependencies: `python3 -m unittest discover -s tests -v`.

Prefer to see every step? The manual method below does the same thing with the original tool.

## Contents

- [Quick start: one command](#quick-start-one-command)
- [Why bother](#why-bother)
- [How it works](#how-it-works)
- [Before you start](#before-you-start)
- [Step by step](#step-by-step)
- [The multi-profile trap](#the-multi-profile-trap)
- [Rolling back](#rolling-back)
- [Troubleshooting](#troubleshooting)
- [Credits](#credits)

## Why bother

On October 22, 2026, The Browser Company emailed Arc members that **Arc will be sunset on December 22, 2026**:

- Arc for macOS and Windows gets only basic updates until then.
- After December 22, you can no longer sign in to your Arc account. All Arc accounts are deactivated, and personal data tied to them is deleted within 30 days.
- The company's recommended path is its AI browser, **Dia**, which imports Arc Spaces (as Profiles), Favorites, pinned tabs and folders, bookmarks, history, passwords and extensions. You can also export a list of your Arc tabs for a different browser.

> [!IMPORTANT]
> **Do this before December 22, 2026.** This guide reads Arc's local sidebar file, and Arc's own import and export options stop working once accounts are deactivated.

If you'd rather not move to Dia, [Zen](https://zen-browser.app/) is the closest free, open-source alternative: vertical tabs, Spaces, pinned folders, Essentials and split view, built on Firefox.

The catch: the most valuable part of Arc, your organised sidebar, has no official way out. Arc can't export pinned tabs, and Zen has no Arc importer. Zen's bookmark import brings over links, but not pinned folders or Spaces.

| In Arc | Becomes in Zen |
|---|---|
| Space | Workspace (Space), same emoji icon |
| Folder, including nested folders | Pinned folder, nesting preserved |
| Pinned tab at the top level of a Space | Pinned tab |
| Favorites (the icon row) | Essentials |
| History, passwords, extensions, unpinned tabs | *Not migrated*. Use Zen's own import tools. |

## How it works

```
Arc: StorableSidebar.json  →  migrate.py  →  Zen: zen-sessions.jsonlz4
```

Arc stores every Space, folder and pinned tab as plain JSON. Zen keeps its whole tab, folder and Space state in a single compressed (mozLz4) JSON file inside your profile. The open-source [arc-to-zen-migration](https://github.com/shub-rajput/arc-to-zen-migration) tool reads the Arc file and writes the Zen one.

Because it edits Zen's session file directly, **Zen must be fully closed** while it runs, and you need a backup.

## Before you start

- **macOS**, with **Arc still installed** and your sidebar synced on this Mac
- **Zen installed and opened at least once**
- **Python 3** (it comes with Apple's Command Line Tools; if macOS offers to install them, accept)
- **[Homebrew](https://brew.sh/)**, for the `lz4` library

> [!WARNING]
> The tool was written for Zen 1.20.x. It worked for me on a newer Zen in October 2026, but Zen's session format can change between versions. That's why steps 1 and 7 exist.

## Step by step

This is the manual method. The [one-command tool](#quick-start-one-command) does all of it for you. All commands run in **Terminal** (Applications → Utilities).

### 1. Quit Zen and back up your whole Zen folder

Press **Cmd+Q** in Zen. Closing the window isn't enough. Then:

```bash
cp -R ~/Library/"Application Support"/zen ~/zen-backup
```

Run it once. If `~/zen-backup` already exists, a second run nests another copy inside it.

### 2. Install lz4 and download the tool

```bash
brew install lz4
git clone https://github.com/shub-rajput/arc-to-zen-migration.git ~/arc-to-zen
cd ~/arc-to-zen/scripts
```

### 3. Find the profile Zen actually uses

**Don't skip this.** If you've ever reinstalled Zen, or have both Zen and Zen Twilight, you probably have several profiles, and the tool silently picks the first one it finds (see [the trap](#the-multi-profile-trap)).

Open Zen, create a Space with a distinctive name (for example *Test*), then **quit Zen with Cmd+Q** so it saves. Then list every profile and its Spaces:

```bash
for p in ~/Library/"Application Support"/zen/Profiles/*/; do
  echo "== $p"
  ls -l "$p"zen-sessions.jsonlz4 2>/dev/null
  python3 ~/arc-to-zen/scripts/zen_session.py "$p" 2>&1 | head -1
done
cat ~/Library/"Application Support"/zen/installs.ini
```

The right profile is the one whose `Spaces:` line shows the Space you just created and whose session file has the newest timestamp. Copy its folder name, for example `27darc2s.Default (release)-1`.

### 4. Preview what Arc has

```bash
python3 migrate.py --list
```

You should see your Arc Spaces, for example `Arc spaces : ['Personal', 'Work']`. Ignore the "Zen spaces" line here. It reflects whichever profile the tool picked by default.

### 5. Migrate into that exact profile

The stock `migrate.py` has no option for choosing a profile, so this one-liner points it at the right one. Replace `YOUR-PROFILE-FOLDER` with the folder name from step 3:

```bash
cd ~/arc-to-zen/scripts && python3 -c '
import sys, migrate, zen_session
p = zen_session.Path.home() / "Library/Application Support/zen/Profiles/YOUR-PROFILE-FOLDER"
migrate.find_profiles = lambda: [p]
sys.argv = ["migrate.py", "--all", "--favorites"]
migrate.main()'
```

Expected output:

```
Backup: …/YOUR-PROFILE-FOLDER/zen-sessions.jsonlz4.bak
  Personal: 28 folders, 102 pins, 0 essentials
  Work: 26 folders, 65 pins, 0 essentials
  favorites: 26 global essentials
All spaces migrated.
```

- Spaces that already exist in Zen with the same name get **filled**, and their current pinned tabs are replaced.
- Missing Spaces are created.
- Leave out `"--favorites"` if you don't want Arc's Favorites as Essentials.

### 6. Make Essentials appear in every Space (optional)

Arc's Favorites are global. Zen's Essentials are per-Space by default, so the imported ones may only show up in one Space. With Zen still closed, run:

```bash
echo 'user_pref("zen.workspaces.separate-essentials", false);' >> \
  ~/Library/"Application Support"/zen/Profiles/"YOUR-PROFILE-FOLDER"/user.js
```

### 7. Open Zen and check

Your Spaces should be in the sidebar with every folder and subfolder in Arc's order. Tabs load the first time you click them.

Keep `~/zen-backup` for a few days, then delete it and the `~/arc-to-zen` folder.

## The multi-profile trap

My first run reported success (*"Personal: 28 folders, 102 pins… All spaces migrated."*), but Zen opened exactly as before. Nothing had changed.

The clue was in the preview: `Zen spaces : []`. The profile the tool looked at had no Spaces at all, but the Zen I was using obviously did. Listing every profile showed three folders, and `installs.ini` listed **two separate Zen installs**, each with its own default profile:

| Profile folder | What it was |
|---|---|
| `1v1hluxd.Default (release)` | Old install. The tool picked this one because it came first. |
| `27darc2s.Default (release)-1` | **The profile Zen was actually using**, with the test Space and the newest session file |
| `nwy290ny.Default Profile` | Leftover with no session file |

The tool's `find_profiles()` collects every profile that has a session file and uses `[0]`. On a Mac that has had more than one Zen install, that's a coin toss. Pointing it at the right folder (step 5) fixed it on the next run.

> [!TIP]
> If the tool says "migrated" but Zen looks unchanged, it wrote to a different profile. Your real profile hasn't been touched, so just repeat step 5 with the right folder.

## Rolling back

Quit Zen first. Then restore the tool's automatic backup:

```bash
cd ~/Library/"Application Support"/zen/Profiles/"YOUR-PROFILE-FOLDER"
cp zen-sessions.jsonlz4.bak zen-sessions.jsonlz4
```

Or restore from your own full backup from step 1:

```bash
cp ~/zen-backup/Profiles/"YOUR-PROFILE-FOLDER"/zen-sessions.jsonlz4 \
   ~/Library/"Application Support"/zen/Profiles/"YOUR-PROFILE-FOLDER"/
```

> [!WARNING]
> Every run overwrites `zen-sessions.jsonlz4.bak`. If you run the migration twice, the `.bak` holds the result of the first run, not your original Zen. That's why step 1 makes a full backup.

## Troubleshooting

<details>
<summary><b>"Migrated" but Zen looks exactly the same</b></summary>

It wrote to another profile. See [the multi-profile trap](#the-multi-profile-trap) and redo steps 3 and 5. Also make sure Zen was fully quit (Cmd+Q). If Zen is running, it overwrites the session file when it closes.
</details>

<details>
<summary><b><code>cd: too many arguments</code></b></summary>

`Profiles/*/` expands to every profile folder when you have more than one. Use the exact folder name in quotes, because these names contain spaces and brackets.
</details>

<details>
<summary><b><code>liblz4 not found</code> or <code>brew: command not found</code></b></summary>

Install [Homebrew](https://brew.sh/), then run `brew install lz4`. The script looks for `/opt/homebrew/lib/liblz4.dylib` (Apple Silicon) or `/usr/local/lib/liblz4.dylib` (Intel).
</details>

<details>
<summary><b>Folders appear but are empty, or tabs land in the wrong Space</b></summary>

Zen has probably changed its session format. Restore your backup, create one folder with two tabs by hand in Zen, quit, and run `python3 zen_session.py "<profile>" --dump`. Compare its structure with the builders in `zen_session.py`, or open an issue on the tool's repo.
</details>

<details>
<summary><b>I only want the links, not the pinned setup</b></summary>

Use [arc-export](https://github.com/ivnvxd/arc-export). It turns `StorableSidebar.json` into a standard bookmarks HTML file, which you can import in Zen via **Cmd+Shift+O → Import and Backup → Import Bookmarks from HTML**. It's safer, but you'll pin everything yourself.
</details>

<details>
<summary><b>Arc isn't on this Mac any more</b></summary>

Copy `~/Library/Application Support/Arc/StorableSidebar.json` from the old Mac or a backup. Arc also keeps dated copies like `StorableSidebar.2026-10-….json` in that folder. Use the undated one, which is the current state.
</details>

## Credits

- `arc2zen.py`, the one-command tool in this repo, adapts its Zen session builders from arc-to-zen-migration (MIT). The notice is in the file header.
- [arc-to-zen-migration](https://github.com/shub-rajput/arc-to-zen-migration) by shub-rajput (MIT) does the actual migration
- [arc-export](https://github.com/ivnvxd/arc-export) by ivnvxd (MIT) exports Arc pinned tabs as bookmarks
- [Zen Browser](https://zen-browser.app/), a free, open-source browser built on Firefox

This guide documents my own migration and the fix for the profile issue. It isn't affiliated with The Browser Company or the Zen project.
