#!/bin/zsh
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  ".venv/bin/python" crawler_app.py
elif [ -x "/opt/homebrew/bin/python3" ]; then
  "/opt/homebrew/bin/python3" crawler_app.py
elif [ -x "/usr/local/bin/python3" ]; then
  "/usr/local/bin/python3" crawler_app.py
elif [ -x /usr/bin/python3 ]; then
  /usr/bin/python3 crawler_app.py
else
  python3 crawler_app.py
fi
