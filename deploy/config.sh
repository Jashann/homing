# Shared settings for deploy.sh, secrets.sh and teardown.sh. Sourced, not run.
# The Google Cloud project comes from the environment or the active gcloud configuration, never from this file:
# HOMING_GCP_PROJECT, else GOOGLE_CLOUD_PROJECT, else `gcloud config get-value project`.
PROJECT="${HOMING_GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}"
[ -n "$PROJECT" ] || PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [ -z "$PROJECT" ] || [ "$PROJECT" = "(unset)" ]; then
  echo "No Google Cloud project: set GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID (or HOMING_GCP_PROJECT), or run" \
       "gcloud config set project YOUR_PROJECT_ID" >&2
  exit 2
fi
REGION="${HOMING_GCP_REGION:-us-central1}"
SERVICE="${HOMING_SERVICE:-homing}"
REPO="homing"                                   # Artifact Registry repository, used only by Homing
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/server"
RUN_SA_NAME="homing-run"
RUN_SA="${RUN_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
STAGING_PREFIX="gs://${PROJECT}_cloudbuild/homing"   # Cloud Build source uploads for Homing
LABEL="project=homing"
HOLDER_EMAIL="sb-8rtn549532902@business.example.com"

# Secret Manager names -> the environment variables the server reads (src/homing/paypal.py, server.py)
SECRET_PAYER_ID="homing-paypal-client-id"
SECRET_PAYER_SECRET="homing-paypal-secret"
SECRET_INVOICER_ID="homing-paypal-invoicer-client-id"
SECRET_INVOICER_SECRET="homing-paypal-invoicer-secret"
SECRETS_FLAG="PAYPAL_CLIENT_ID=${SECRET_PAYER_ID}:latest,PAYPAL_SECRET=${SECRET_PAYER_SECRET}:latest,PAYPAL_INVOICER_CLIENT_ID=${SECRET_INVOICER_ID}:latest,PAYPAL_INVOICER_SECRET=${SECRET_INVOICER_SECRET}:latest"

gc() { gcloud --project "$PROJECT" --quiet "$@"; }
