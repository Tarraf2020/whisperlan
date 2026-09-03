#!/usr/bin/env bash
# whisperlan installer — works locally AND via curl:
#   curl -fsSL https://raw.githubusercontent.com/Tarraf2020/whisperlan/main/install.sh | bash
#   (replace Tarraf2020/whisperlan with your repo if you forked it)
set -euo pipefail

REPO="${REPO:-Tarraf2020/whisperlan}"
BRANCH="${BRANCH:-main}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
NAME="whisperlan"

echo "🤫 installing whisperlan..."

mkdir -p "$BIN_DIR"

if [ -f "$(dirname "$0")/whisper.py" ]; then
  # local install (you cloned the repo)
  cp "$(dirname "$0")/whisper.py" "$BIN_DIR/$NAME"
else
  # remote install (curl | bash)
  URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/whisper.py"
  echo "  downloading $URL"
  curl -fsSL "$URL" -o "$BIN_DIR/$NAME"
fi

chmod +x "$BIN_DIR/$NAME"
# short alias still works: `whisper` == `whisperlan`
ln -sf "$BIN_DIR/$NAME" "$BIN_DIR/whisper"

# make sure BIN_DIR is on PATH
if ! echo ":$PATH:" | grep -q ":$BIN_DIR:"; then
  echo "  adding $BIN_DIR to PATH in ~/.zshrc and ~/.bashrc"
  echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$HOME/.zshrc"
  echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$HOME/.bashrc"
  export PATH="$BIN_DIR:$PATH"
fi

echo ""
"$BIN_DIR/$NAME" --version
echo ""
echo "✅ done! open a NEW terminal, then type:"
echo ""
echo "   whisperlan --name ali   (or short: whisper --name ali)"
echo ""
echo "  (same WiFi + same --port on all machines)"
