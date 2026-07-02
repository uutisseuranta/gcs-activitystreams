#!/usr/bin/env bash
# deploy/deploy.sh
# Cloud Run Job / Service deploy-ajuri.
# Käyttö: ./deploy/deploy.sh <palvelun-nimi> [job|service]
# Esimerkki: ./deploy/deploy.sh rss-fetch-job job

set -euo pipefail

SERVICE="${1:-}"
TYPE="${2:-job}"

if [ -z "$SERVICE" ]; then
  echo "Virhe: anna palvelun nimi (esim. rss-fetch-job)" >&2
  exit 1
fi

ENV_FILE="deploy/${SERVICE}.env.yaml"
if [ ! -f "$ENV_FILE" ]; then
  echo "Virhe: Konfiguraatiotiedostoa $ENV_FILE ei löydy." >&2
  exit 1
fi

# Luetaan tarvittavat perusarvot YAML-tiedostosta yksinkertaisella parserilla
PROJECT=$(grep 'GCP_PROJECT:' "$ENV_FILE" | awk -F': ' '{print $2}' | tr -d '"'"'")
REGION=$(grep 'BQ_LOCATION:' "$ENV_FILE" | awk -F': ' '{print $2}' | tr -d '"'"'")
SA_EMAIL=$(grep 'SERVICE_ACCOUNT_EMAIL:' "$ENV_FILE" | awk -F': ' '{print $2}' | tr -d '"'"'")

IMAGE="europe-north1-docker.pkg.dev/${PROJECT}/jobs/${SERVICE}:latest"

echo "=== Deploying $SERVICE as $TYPE ==="
echo "Project: $PROJECT"
echo "Region:  $REGION"
echo "Image:   $IMAGE"
echo "SA:      $SA_EMAIL"
echo "=================================="

# Varmistetaan, että Artifact Registryn repo on luotu
gcloud artifacts repositories create jobs \
  --repository-format=docker \
  --location=europe-north1 \
  --project="$PROJECT" 2>/dev/null || true

# Rakennetaan Docker-kontti ja pushataan se Artifact Registryyn
# (Yritetään ensin alaviivaversiota src/rss_fetch_job, sitten väliviivaversiota)
SRC_DIR="src/${SERVICE//-/_}"
if [ ! -d "$SRC_DIR" ]; then
  SRC_DIR="src/${SERVICE}"
fi

if [ ! -d "$SRC_DIR" ]; then
  echo "Virhe: Lähdekoodihakemistoa $SRC_DIR (tai alaviivaversiota) ei löydy." >&2
  exit 1
fi

echo "Building and pushing container using Cloud Build..."
gcloud builds submit --tag "$IMAGE" "$SRC_DIR" --project="$PROJECT" --gcs-source-staging-dir="gs://${PROJECT}-builds/source"

if [ "$TYPE" = "job" ]; then
  echo "Deploying Cloud Run Job..."
  gcloud run jobs deploy "$SERVICE" \
    --image "$IMAGE" \
    --region "$REGION" \
    --env-vars-file "$ENV_FILE" \
    --service-account "$SA_EMAIL" \
    --project "$PROJECT"
else
  echo "Deploying Cloud Run Service..."
  gcloud run deploy "$SERVICE" \
    --image "$IMAGE" \
    --region "$REGION" \
    --env-vars-file "$ENV_FILE" \
    --service-account "$SA_EMAIL" \
    --project "$PROJECT" \
    --no-allow-unauthenticated
fi

echo "Deploy completed successfully!"
