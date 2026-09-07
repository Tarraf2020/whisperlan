#!/usr/bin/env bash
# whisper uninstaller — removes ~/.local/bin copy; brew copy needs `brew uninstall`
set -euo pipefail
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
rm -f "$BIN_DIR/whisper" "$BIN_DIR/whisperlan"
echo "🫡 whisper removed ($BIN_DIR/whisper, $BIN_DIR/whisperlan)."
echo "  (brew install? also run: brew uninstall whisperlan)"
echo "  (kept: ~/.whisper-cert.pem, ~/.whisper-key.pem — delete manually if you want)"
