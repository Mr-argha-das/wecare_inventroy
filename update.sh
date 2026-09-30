#!/usr/bin/env bash
# One-command production update for WE CARE billing.
#
#   ./update.sh                 # pulls main, installs deps, restarts PM2
#   BRANCH=some-branch ./update.sh
#   VENV=env ./update.sh        # if your virtualenv folder is named "env"
#
# Safe by default: stops the app, snapshots data + uploads, then restarts.
set -euo pipefail

cd "$(dirname "$0")"

BRANCH="${BRANCH:-main}"
APP_NAME="${APP_NAME:-wecare-billing}"

# Auto-detect the virtualenv folder (.venv or env)
if [ -n "${VENV:-}" ]; then
  VENV_DIR="$VENV"
elif [ -x ".venv/bin/python" ]; then
  VENV_DIR=".venv"
elif [ -x "env/bin/python" ]; then
  VENV_DIR="env"
else
  echo "!! No virtualenv found (.venv or env). Create one first:"
  echo "   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
PY="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

# Which PM2 process is running this app?
if command -v pm2 >/dev/null 2>&1; then
  if ! pm2 describe "$APP_NAME" >/dev/null 2>&1; then
    DETECTED="$(pm2 jlist 2>/dev/null | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print(d[0]["name"] if d else "")' 2>/dev/null || true)"
    [ -n "$DETECTED" ] && APP_NAME="$DETECTED"
  fi
fi

if ! command -v pm2 >/dev/null 2>&1; then
  echo "!! PM2 not found. Install it first:  sudo npm install -g pm2"
  exit 1
fi

STAMP="$(date +%F_%H%M%S)"
echo "==> App: $APP_NAME | branch: $BRANCH | venv: $VENV_DIR"

echo "==> Stopping app"
pm2 stop "$APP_NAME" >/dev/null 2>&1 || echo "   (not running under PM2 — continuing)"

echo "==> Backing up data + uploads -> backups/pre_update_$STAMP"
mkdir -p "backups/pre_update_$STAMP"
cp -r data "backups/pre_update_$STAMP/data" 2>/dev/null || true
cp -r app/static/uploads "backups/pre_update_$STAMP/uploads" 2>/dev/null || true

echo "==> Pulling $BRANCH"
git pull origin "$BRANCH"

echo "==> Installing dependencies"
"$PIP" install -q -r requirements.txt

echo "==> Starting app"
if pm2 describe "$APP_NAME" >/dev/null 2>&1; then
  pm2 restart "$APP_NAME" --update-env
else
  pm2 start ecosystem.config.js --env production
fi
pm2 save >/dev/null 2>&1 || true

sleep 3
PORT="${PORT:-8000}"
echo "==> Health check on http://127.0.0.1:$PORT/auth/login"
if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/auth/login"; then
  echo "✅ Update complete — app is responding."
else
  echo "❌ App did not respond. Check: pm2 logs $APP_NAME --lines 80"
  exit 1
fi

cat <<'NEXT'

Next steps in the browser (one time only):
  1. Settings -> Documents  -> select "Template D — We Care" -> Save
  2. Settings -> Company & Brand -> logo, UPI QR, bank details, brand colour -> Save
  3. Open any bill -> Print / Download PDF to verify the new invoice format
NEXT
