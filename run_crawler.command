#!/bin/zsh
cd "$(dirname "$0")"

if [ -x /usr/bin/python3 ]; then
  /usr/bin/python3 crawler_app.py
else
  python3 crawler_app.py
fi
