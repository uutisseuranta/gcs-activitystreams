#!/usr/bin/env bash
# deploy/init-sa.sh
# Alustaa GCP-palvelutilin ja myöntää tarvittavat IAM-oikeudet BigQueryyn.
# Käyttö: ./deploy/init-sa.sh

set -euo pipefail

PROJECT="uutisseuranta-activitystreams"
SA_NAME="backend"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

echo "=== Alustetaan palvelutili ==="
echo "Projekti:      $PROJECT"
echo "Palvelutili:   $SA_EMAIL"
echo "=============================="

# Luodaan palvelutili jos ei ole olemassa
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT" &>/dev/null; then
  echo "Luodaan palvelutili $SA_EMAIL..."
  gcloud iam service-accounts create "$SA_NAME" \
    --description="ActivityStreams backend service account" \
    --display-name="backend" \
    --project="$PROJECT"
else
  echo "Palvelutili on jo olemassa."
fi

# Myönnetään BQ dataEditor -oikeus (taulujen lukeminen ja kirjoittaminen)
echo "Myönnetään roles/bigquery.dataEditor..."
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/bigquery.dataEditor" \
  --quiet >/dev/null

# Myönnetään BQ user -oikeus (kyselyjobien luominen ja ajaminen)
echo "Myönnetään roles/bigquery.user..."
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/bigquery.user" \
  --quiet >/dev/null

# Luodaan sosiaalinen BigQuery dataset jos ei ole olemassa
echo "Varmistetaan sosiaalisen datasetin activitystreams_social olemassaolo..."
bq show --project_id="$PROJECT" "activitystreams_social" &>/dev/null || \
  bq mk --project_id="$PROJECT" --location=europe-north1 --dataset "activitystreams_social"

echo "Palvelutili alustettu ja valtuutettu onnistuneesti!"
