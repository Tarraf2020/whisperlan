#!/usr/bin/env bash
# whisper uninstaller
set -euo pipefail
BIN="${BIN_DIR:-$HOME/.local/bin}/whisper"
rm -f "$BIN"
echo "🫡 whisper removed ($BIN)."
echo "  (kept: ~/.whisper.log, ~/.whisper-cert.pem, ~/.whisper-key.pem — delete manually if you want)"
