#!/usr/bin/env bash
# Deletes everything deploy.sh and secrets.sh created: the Cloud Run service, the Artifact Registry repository and its
# images, the four PayPal secrets, the runtime service account and Homing's Cloud Build source uploads.
#   deploy/teardown.sh          asks first
#   deploy/teardown.sh --yes    no question
# Shared resources in the project (other services, the cloud-run-source-deploy repository) are not touched.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/config.sh"

if [ "${1:-}" != "--yes" ]; then
  read -r -p "Delete Cloud Run service $SERVICE, repository $REPO, the homing-paypal-* secrets, $RUN_SA and $STAGING_PREFIX in $PROJECT? [y/N] " a
  [ "$a" = "y" ] || [ "$a" = "Y" ] || { echo "nothing deleted"; exit 0; }
fi

gc run services delete "$SERVICE" --region "$REGION" && echo "deleted service $SERVICE" || echo "no service $SERVICE"
gc artifacts repositories delete "$REPO" --location "$REGION" && echo "deleted repository $REPO" || echo "no repository $REPO"
for s in "$SECRET_PAYER_ID" "$SECRET_PAYER_SECRET" "$SECRET_INVOICER_ID" "$SECRET_INVOICER_SECRET"; do
  gc secrets delete "$s" && echo "deleted secret $s" || echo "no secret $s"
done
gc iam service-accounts delete "$RUN_SA" && echo "deleted $RUN_SA" || echo "no $RUN_SA"
gcloud storage rm -r "$STAGING_PREFIX" --project "$PROJECT" --quiet 2>/dev/null && echo "deleted $STAGING_PREFIX" \
  || echo "no uploads under $STAGING_PREFIX"
echo "Build records (tag homing) stay in Cloud Build history; they cost nothing. List them with:"
echo "  gcloud builds list --project $PROJECT --region $REGION --filter 'tags=homing'"
