#!/usr/bin/env bash
# Puts the PayPal sandbox keys into Secret Manager, labelled project=homing, readable only by Homing's runtime account.
#   deploy/secrets.sh            create any missing secret from ~/.paypal_sandbox and ~/.paypal_sandbox_homing
#   deploy/secrets.sh --refresh  also add a new version of every secret from the same files
# The values go from the file to gcloud through a pipe (--data-file=-): never printed, logged or written anywhere.
# Creating the secrets does not attach them; deploy/deploy.sh <src> --with-paypal (or the command in deploy/NOTES.md)
# does that.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/config.sh"
REFRESH="${1:-}"
PAYER_FILE="${PAYPAL_PAYER_FILE:-$HOME/.paypal_sandbox}"
INVOICER_FILE="${PAYPAL_INVOICER_FILE:-$HOME/.paypal_sandbox_homing}"

# Prints one value from a KEY=value file (the format src/homing/paypal.py reads), with no newline, to stdout only.
value() {
  python3 -I -c '
import sys
want = sys.argv[2].split(",")
vals = {}
for line in open(sys.argv[1]).read().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        vals[k.strip().upper()] = v.strip().strip("\"").strip("'"'"'")
for k in want:
    if vals.get(k):
        sys.stdout.write(vals[k]); sys.exit(0)
sys.exit("missing " + "/".join(want) + " in " + sys.argv[1])
' "$1" "$2"
}

gc iam service-accounts describe "$RUN_SA" >/dev/null 2>&1 || \
  gc iam service-accounts create "$RUN_SA_NAME" --display-name "Homing demo (Cloud Run, project=homing)"

put() {  # secret_name file keys
  local name="$1" file="$2" keys="$3"
  [ -f "$file" ] || { echo "skip $name: $file not found"; return; }
  if ! gc secrets describe "$name" >/dev/null 2>&1; then
    gc secrets create "$name" --replication-policy automatic --labels "$LABEL" >/dev/null
    value "$file" "$keys" | gc secrets versions add "$name" --data-file=- >/dev/null
    echo "created $name"
  elif [ "$REFRESH" = "--refresh" ]; then
    value "$file" "$keys" | gc secrets versions add "$name" --data-file=- >/dev/null
    echo "added a new version of $name"
  else
    echo "exists  $name"
  fi
  gc secrets add-iam-policy-binding "$name" --member "serviceAccount:$RUN_SA" \
    --role roles/secretmanager.secretAccessor >/dev/null
}

put "$SECRET_PAYER_ID"        "$PAYER_FILE"    CLIENT_ID,PAYPAL_CLIENT_ID
put "$SECRET_PAYER_SECRET"    "$PAYER_FILE"    SECRET,PAYPAL_SECRET
put "$SECRET_INVOICER_ID"     "$INVOICER_FILE" CLIENT_ID,PAYPAL_CLIENT_ID
put "$SECRET_INVOICER_SECRET" "$INVOICER_FILE" SECRET,PAYPAL_SECRET
