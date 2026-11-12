#!/usr/bin/env bash
# Builds Homing from a source folder with Cloud Build and deploys it to Cloud Run as the public demo.
#
#   deploy/deploy.sh <source_dir>                  build + deploy; PayPal secrets left as they are on the service
#   deploy/deploy.sh <source_dir> --with-paypal    also attach the PayPal sandbox secrets (only once the public guard
#                                                  in deploy/PUBLIC_GUARD.md is in the server)
#   deploy/deploy.sh <source_dir> --without-paypal also detach them (paying then fails closed)
#
# <source_dir> is a Homing checkout (a git worktree, an archive or the working folder). Only what the image needs is
# uploaded; local databases, uploads, node_modules and .venv are left out. If <source_dir> has no Dockerfile, the
# Dockerfile and .dockerignore next to this script's folder are used.
# Every resource is labelled project=homing. Run deploy/secrets.sh once before --with-paypal.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/config.sh"

SRC="${1:-}"; MODE="${2:-}"
[ -n "$SRC" ] && [ -d "$SRC" ] || { echo "usage: deploy/deploy.sh <source_dir> [--with-paypal|--without-paypal]"; exit 2; }
SRC="$(cd "$SRC" && pwd)"
case "$MODE" in ""|--with-paypal|--without-paypal) ;; *) echo "unknown option $MODE"; exit 2;; esac
for f in pyproject.toml uv.lock src/homing/server.py web/package.json data/sample_ledger.json data/saved_readings.json; do
  [ -f "$SRC/$f" ] || { echo "$SRC does not look like Homing: $f is missing"; exit 2; }
done

# ---- one-time resources (idempotent)
if ! gc artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1; then
  echo "Creating Artifact Registry repository $REPO"
  gc artifacts repositories create "$REPO" --location "$REGION" --repository-format docker \
    --labels "$LABEL" --description "Homing demo images"
  cat > "${TMPDIR:-/tmp}/homing-cleanup.json" <<'JSON'
[{"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 2}},
 {"name": "delete-rest", "action": {"type": "Delete"}, "condition": {"tagState": "any"}}]
JSON
  gc artifacts repositories set-cleanup-policies "$REPO" --location "$REGION" \
    --policy "${TMPDIR:-/tmp}/homing-cleanup.json" --no-dry-run >/dev/null
fi
if ! gc iam service-accounts describe "$RUN_SA" >/dev/null 2>&1; then
  echo "Creating runtime service account $RUN_SA (no project roles; it may read only Homing's secrets)"
  gc iam service-accounts create "$RUN_SA_NAME" --display-name "Homing demo (Cloud Run, project=homing)"
fi

# ---- stage the build context
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/homing-build.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
rsync -a \
  --exclude .git --exclude .venv --exclude node_modules --exclude __pycache__ --exclude .pytest_cache \
  --exclude 'web/dist' --exclude 'web/shots' --exclude '.env' --exclude '.env.*' \
  --exclude 'data/*.sqlite' --exclude 'data/*.sqlite-*' --exclude 'data/*.duckdb' \
  --exclude 'data/files' --exclude 'data/uploads' --exclude 'data/claims' --exclude 'data/downloads' \
  --exclude tests --exclude docs --exclude deploy \
  "$SRC/" "$STAGE/"
ROOT_OF_SCRIPTS="$(cd "$HERE/.." && pwd)"
[ -f "$STAGE/Dockerfile" ] || cp "$ROOT_OF_SCRIPTS/Dockerfile" "$STAGE/Dockerfile"
[ -f "$STAGE/.dockerignore" ] || cp "$ROOT_OF_SCRIPTS/.dockerignore" "$STAGE/.dockerignore"
printf '.git\n' > "$STAGE/.gcloudignore"   # upload exactly the staged folder

REV="$(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo nogit)"
TAG="$(date -u +%Y%m%d-%H%M%S)-$REV"
cat > "$STAGE/cloudbuild.yaml" <<YAML
steps:
- name: gcr.io/cloud-builders/docker
  args: ["build", "-t", "$IMAGE:$TAG", "-t", "$IMAGE:latest", "."]
images: ["$IMAGE:$TAG", "$IMAGE:latest"]
tags: ["homing"]
options:
  logging: CLOUD_LOGGING_ONLY
timeout: 1200s
YAML

echo "Building $IMAGE:$TAG with Cloud Build"
gc builds submit "$STAGE" --region "$REGION" --config "$STAGE/cloudbuild.yaml" \
  --gcs-source-staging-dir "$STAGING_PREFIX/source"

# ---- deploy
NUMBER="$(gc projects describe "$PROJECT" --format 'value(projectNumber)')"
URL="https://${SERVICE}-${NUMBER}.${REGION}.run.app"
SECRET_ARGS=()
[ "$MODE" = "--with-paypal" ] && SECRET_ARGS=(--update-secrets "$SECRETS_FLAG")
[ "$MODE" = "--without-paypal" ] && SECRET_ARGS=(--clear-secrets)

echo "Deploying $SERVICE to Cloud Run ($REGION)"
gc run deploy "$SERVICE" --image "$IMAGE:$TAG" --region "$REGION" \
  --service-account "$RUN_SA" \
  --cpu 1 --memory 1Gi --min-instances 0 --max-instances 1 --concurrency 40 --timeout 300 \
  --cpu-throttling --cpu-boost --execution-environment gen2 \
  --port 8080 --labels "$LABEL" \
  --update-env-vars "HOMING_DEMO=1,HOMING_PUBLIC=1,HOMING_PUBLIC_DAILY_CAP=300,HOMING_HOLDER_EMAIL=$HOLDER_EMAIL,HOMING_PUBLIC_URL=$URL" \
  --no-invoker-iam-check \
  ${SECRET_ARGS[@]+"${SECRET_ARGS[@]}"}

# Public access: this organisation refuses an allUsers invoker binding (--allow-unauthenticated fails with
# "Setting IAM policy failed"), so the service runs with the invoker IAM check off. Checked again here.
code="$(curl -s -o /dev/null -w '%{http_code}' "$URL/api/meta" || true)"
if [ "$code" = "403" ] || [ "$code" = "401" ]; then
  echo "Unauthenticated access is blocked ($code); turning the invoker IAM check off"
  gc run services update "$SERVICE" --region "$REGION" --no-invoker-iam-check >/dev/null
  sleep 5
  code="$(curl -s -o /dev/null -w '%{http_code}' "$URL/api/meta" || true)"
fi
echo "Deployed: $URL  (GET /api/meta -> HTTP $code)"
