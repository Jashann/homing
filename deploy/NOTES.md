# Hosted demo on Cloud Run

Service `homing` in your Google Cloud project, region `us-central1`, at
`https://homing-<PROJECT_NUMBER>.us-central1.run.app` (deploy.sh prints the address). The scripts read the project from
`HOMING_GCP_PROJECT`, else `GOOGLE_CLOUD_PROJECT`, else `gcloud config get-value project`; none is stored in the repo.

| File | What it does |
|---|---|
| `../Dockerfile`, `../.dockerignore` | One image: Node 22 builds `web/dist`, Python 3.11 + uv runs the server on `$PORT` |
| `config.sh` | Project (from the environment or gcloud), region, names, labels (sourced by the scripts) |
| `secrets.sh` | Puts `~/.paypal_sandbox` and `~/.paypal_sandbox_homing` into Secret Manager (values piped, never printed) |
| `deploy.sh <source_dir> [--with-paypal\|--without-paypal]` | Cloud Build, then `gcloud run deploy` |
| `verify.py <url> <source_dir>` | Checks the hosted demo from outside: the four sample cases, packet, notice, pay fails closed |
| `teardown.sh [--yes]` | Deletes the service, image repository, secrets, runtime account and build uploads |
| `PUBLIC_GUARD.md` | The guard the server needs before the PayPal secrets are attached |

## Commands

```bash
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID          # or: gcloud config set project YOUR_PROJECT_ID
deploy/secrets.sh                                   # once (or --refresh after changing the key files)
deploy/deploy.sh /path/to/homing                    # rebuild + redeploy, PayPal stays as it is on the service
python3 -I deploy/verify.py https://homing-PROJECT_NUMBER.us-central1.run.app /path/to/homing
deploy/deploy.sh /path/to/homing --with-paypal      # only once PUBLIC_GUARD.md is in the server
deploy/teardown.sh
```

## How it runs

- 1 vCPU, 1 GiB, min 0, max 1 instance, concurrency 40, request-based billing (CPU only while a request runs),
  startup CPU boost. Every resource carries the label `project=homing` (the Cloud Build records carry the tag `homing`).
- Environment: `HOMING_DEMO=1`, `HOMING_PUBLIC=1`, `HOMING_PUBLIC_DAILY_CAP=300`,
  `HOMING_HOLDER_EMAIL=sb-8rtn549532902@business.example.com`, `HOMING_PUBLIC_URL=<the service URL>`.
- Public access: the organisation refuses an `allUsers` invoker binding, so the service runs with
  `--no-invoker-iam-check`.
- The SQLite files and uploads are on the container's disk (memory-backed). They are lost when the instance stops,
  after about 15 idle minutes or on a redeploy; the next start loads the sample ledger again.
- No model in the container: the four sample cases use `data/saved_readings.json`; any other document is reported as
  unreadable and the claim goes to a person.
- Runtime identity: `homing-run@YOUR_PROJECT_ID.iam.gserviceaccount.com`, with no project roles, only
  `secretAccessor` on the four `homing-paypal-*` secrets.
