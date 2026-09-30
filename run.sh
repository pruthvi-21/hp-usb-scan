#!/bin/bash
# Start the scanner web app. First run sets everything up.
set -e
cd "$(dirname "$0")"

if [ ! -e /opt/homebrew/lib/libusb-1.0.dylib ] && [ ! -e /usr/local/lib/libusb-1.0.dylib ]; then
  echo "Installing libusb (needed to talk to the printer over USB)..."
  brew install libusb
fi

if [ ! -x .venv/bin/python ]; then
  echo "Setting up Python environment..."
  python3 -m venv .venv
fi
# (Re)install whenever requirements.txt changes
if [ requirements.txt -nt .venv/.installed ]; then
  .venv/bin/pip install -q -r requirements.txt
  touch .venv/.installed
fi

exec .venv/bin/python -u server.py
