#!/bin/bash
# arc2zen — double-click launcher for Mac (no Terminal needed).
#
# Move your Arc sidebar into Zen Browser. Double-click this file in Finder.
# The first time, right-click it and choose "Open" so macOS lets it run.
#
# It downloads the arc2zen helper next to itself (if needed) and runs it.
# Guide and source: https://github.com/Glebelov96/arc-to-zen-guide

cd "$(dirname "$0")" || exit 1

echo "────────────────────────────────────────────"
echo "  arc2zen — move your Arc sidebar into Zen"
echo "────────────────────────────────────────────"
echo

if [ ! -f arc2zen.py ]; then
  echo "Getting the latest helper…"
  if ! curl -fsSLO https://raw.githubusercontent.com/Glebelov96/arc-to-zen-guide/main/arc2zen.py; then
    echo
    echo "Couldn't download it. Check your internet connection and try again."
    echo "Press any key to close."; read -r -n 1; exit 1
  fi
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "This needs Python 3. macOS will now offer to install the developer tools."
  echo "Click Install, wait for it to finish, then double-click this file again."
  xcode-select --install 2>/dev/null
  echo; echo "Press any key to close."; read -r -n 1; exit 1
fi

python3 arc2zen.py "$@"

echo
echo "────────────────────────────────────────────"
echo "You can close this window."
echo "Press any key to close."
read -r -n 1
