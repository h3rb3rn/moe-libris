#!/usr/bin/env bash
# =============================================================================
#  sync-to-publish.sh — Sync moe-libris dev repo to publish-ready GitHub repo
#
#  Usage: bash scripts/sync-to-publish.sh [--dry-run]
#
#  Copies all code from the dev repo (/opt/moe-libris)
#  to the GitHub publish repo (/opt/deployment/Github/moe-libris),
#  excluding sensitive files, runtime data, and local configuration.
#  After sync, sanitizes hardcoded paths and checks for leaked secrets.
# =============================================================================
set -euo pipefail

DEV_DIR="/opt/moe-libris"
PUB_DIR="/opt/deployment/Github/moe-libris"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "[DRY RUN] No files will be modified."
fi

# Verify both directories exist
if [[ ! -d "$DEV_DIR" ]]; then
  echo "[ERROR] Dev directory not found: $DEV_DIR"
  exit 1
fi
if [[ ! -d "$PUB_DIR" ]]; then
  echo "[ERROR] Publish directory not found: $PUB_DIR"
  exit 1
fi

echo "════════════════════════════════════════════════════"
echo "  MoE Libris — Sync: DEV → PUBLISH"
echo "  From: $DEV_DIR"
echo "  To:   $PUB_DIR"
echo "════════════════════════════════════════════════════"

# ─── Step 1: rsync (exclude sensitive files) ──────────────────────────────

RSYNC_EXCLUDES=(
  --exclude='.git'
  --exclude='.env'
  --exclude='.env.*'
  --exclude='!.env.example'
  --exclude='*.db'
  --exclude='*.sqlite*'
  --exclude='__pycache__'
  --exclude='*.pyc'
  --exclude='node_modules'
  --exclude='.claude/'
  --exclude='*.log'
  --exclude='data/'
  --exclude='.vscode/'
  --exclude='.idea/'
  --exclude='*.pem'
  --exclude='*.key'
  --exclude='*.cert'
  --exclude='registry-cache/'
  --exclude='docker-compose.override.yml'
  --exclude='.pytest_cache/'
  --exclude='.ai_context.md'
  --exclude='tmp/'
)

echo ""
echo "[1/4] Syncing files..."

if $DRY_RUN; then
  rsync -avn --delete "${RSYNC_EXCLUDES[@]}" "$DEV_DIR/" "$PUB_DIR/" | tail -20
  echo "  ... (showing last 20 lines of dry-run output)"
else
  rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$DEV_DIR/" "$PUB_DIR/"
  echo "  Files synced ✓"
fi

# ─── Step 2: Sanitize hardcoded paths ─────────────────────────────────────

echo "[2/4] Sanitizing hardcoded paths..."

SANITIZE_PATTERNS=(
  "/opt/moe-libris|/opt/moe-libris|*.py,*.sh,*.service"
  "/opt/moe-sovereign|/opt/moe-sovereign|*.py,*.sh,*.service"
)

if ! $DRY_RUN; then
  for entry in "${SANITIZE_PATTERNS[@]}"; do
    IFS='|' read -r pattern replacement globs <<< "$entry"
    IFS=',' read -ra glob_arr <<< "$globs"
    for g in "${glob_arr[@]}"; do
      find "$PUB_DIR" -name "$g" -not -path '*/.git/*' -exec \
        sed -i "s|${pattern}|${replacement}|g" {} + 2>/dev/null || true
    done
  done
  echo "  Paths sanitized ✓"
else
  echo "  (skipped in dry-run)"
fi

# ─── Step 3: Sanitize hardcoded IPs ───────────────────────────────────────

echo "[3/4] Checking for leaked IPs..."

LEAKED=$(grep -rn "192\.168\.155\." "$PUB_DIR" \
  --include="*.py" --include="*.yml" --include="*.yaml" \
  --include="*.json" --include="*.sh" --include="*.service" \
  2>/dev/null | grep -v "\.git/" || true)

if [[ -n "$LEAKED" ]]; then
  echo "  ⚠️  WARNING: Real IPs found in publish repo!"
  echo "$LEAKED" | head -10
  echo ""
  echo "  Fix these manually before pushing."
else
  echo "  No leaked IPs found ✓"
fi

# ─── Step 4: Check for leaked secrets ─────────────────────────────────────

echo "[4/4] Checking for leaked secrets..."

SECRETS=$(grep -rn \
  -e "moe-sk-[a-f0-9]\{40,\}" \
  -e "lbk-[a-f0-9]\{40,\}" \
  -e "password.*=.*[a-f0-9]\{20,\}" \
  "$PUB_DIR" \
  --include="*.py" --include="*.yml" --include="*.yaml" \
  --include="*.json" --include="*.sh" --include="*.service" \
  2>/dev/null | grep -v "\.git/" | grep -v "\.example" || true)

if [[ -n "$SECRETS" ]]; then
  echo "  ⚠️  WARNING: Potential real secrets found!"
  echo "$SECRETS" | head -5
  echo ""
  echo "  Fix these manually before pushing."
else
  echo "  No leaked secrets found ✓"
fi

# ─── Summary ──────────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════════════════"
if $DRY_RUN; then
  echo "  DRY RUN complete. No files were modified."
  echo "  Run without --dry-run to apply changes."
else
  PUB_SIZE=$(du -sh "$PUB_DIR" --exclude=.git | awk '{print $1}')
  FILE_COUNT=$(find "$PUB_DIR" -type f -not -path '*/.git/*' | wc -l)
  echo "  Sync complete!"
  echo "  Files: $FILE_COUNT | Size: $PUB_SIZE"
  echo ""
  echo "  Next steps:"
  echo "    cd $PUB_DIR"
  echo "    git add -A"
  echo "    git diff --cached --stat"
  echo "    git commit -m 'Sync from dev: <description>'"
  echo "    git push origin main"
fi
echo "════════════════════════════════════════════════════"
