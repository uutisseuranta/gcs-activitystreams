# Terraform Import – bq-activitystreams

Tämä tiedosto dokumentoi `terraform import` -komennot kaikille resursseille
jotka saattavat olla olemassa Google Cloudissa tai GitHubissa ennen
`terraform apply`:n ensimmäistä ajoa.

Bq-activitystreams on backend-repo: importoitavia resursseja on sekä
GCP-puolella (palvelutili, Artifact Registry, Cloud Run, Scheduler)
että GitHub-puolella (branch protection, labelit).

## Edellytykset

```bash
cd terraform/

# GitHub
export GITHUB_TOKEN="ghp_..."

# Google Cloud – käytä Workload Identity Federationia (WIF) tai avaintiedostoa
export GOOGLE_APPLICATION_CREDENTIALS="~/.config/gcloud/application_default_credentials.json"
# TAI: gcloud auth application-default login

# Aseta muuttujat
cp terraform.tfvars.example terraform.tfvars
# Muokkaa terraform.tfvars: google_client_id, bq_dataset jne.

terraform init
```

---

## 1. IAM – Palvelutili

Jos `deploy/init-sa.sh` on jo ajettu, palvelutili on olemassa.

```bash
# Muoto: projects/<project>/serviceAccounts/<sa_email>
terraform import google_service_account.deploy \
  "projects/uutisseuranta-activitystreams/serviceAccounts/deploy-sa@uutisseuranta-activitystreams.iam.gserviceaccount.com"
```

**IAM-bindingeille ei voi käyttää importia** – ne luodaan aina uusina (idempotent).
Jos Terraform haluaa poistaa olemassa olevan bindingin, tarkista
`terraform plan` -tulostus ennen `apply`:tä.

---

## 2. Artifact Registry

```bash
# Muoto: projects/<project>/locations/<location>/repositories/<name>
terraform import google_artifact_registry_repository.docker \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/repositories/uutisseuranta"
```

> **Huom:** `prevent_destroy = true` on asetettu. Terraform ei anna poistaa
> repositoriota vahingossa.

---

## 3. Cloud Run – Palvelut

Jos palvelut on jo otettu käyttöön (Cloud Console tai `gcloud run deploy`):

```bash
# Muoto: locations/<region>/namespaces/<project>/services/<name>
terraform import google_cloud_run_v2_service.query_api \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/query-api"

terraform import google_cloud_run_v2_service.write_api \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/write-api"

terraform import google_cloud_run_v2_service.og_scraper \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/og-scraper"
```

---

## 4. Cloud Run – Jobit

```bash
terraform import google_cloud_run_v2_job.rss_fetch \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/rss-fetch-job"

terraform import google_cloud_run_v2_job.voikko \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/voikko-job"

terraform import google_cloud_run_v2_job.og_enrichment \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/og-enrichment-job"

terraform import google_cloud_run_v2_job.likes_and_updated \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/likes-and-updated-job"
```

---

## 5. Cloud Scheduler

```bash
# Muoto: projects/<project>/locations/<region>/jobs/<name>
terraform import google_cloud_scheduler_job.rss_pipeline \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/rss-pipeline"

terraform import google_cloud_scheduler_job.og_enrichment_schedule \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/og-enrichment-schedule"

terraform import google_cloud_scheduler_job.likes_and_updated_schedule \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/likes-and-updated-schedule"
```

---

## 6. GitHub Branch Protection

```bash
terraform import github_branch_protection.main "bq-activitystreams:main"
```

---

## 7. GitHub Labelit – Shell-skripti

```bash
#!/usr/bin/env bash
# Tallenna: terraform/import_labels.sh
# Käyttö: bash import_labels.sh

set -e
REPO="bq-activitystreams"

declare -A LABELS
# Jira-sync
LABELS["priority_highest"]="priority:highest"
LABELS["priority_high_jira"]="priority:high"
LABELS["priority_medium"]="priority:medium"
LABELS["priority_low"]="priority:low"
LABELS["priority_lowest"]="priority:lowest"
LABELS["sprint_1"]="sprint:1"
LABELS["sprint_2"]="sprint:2"
LABELS["sprint_3"]="sprint:3"
LABELS["sprint_4"]="sprint:4"
LABELS["sprint_5"]="sprint:5"
LABELS["status_todo"]="status:to-do"
LABELS["status_in_progress"]="status:in-progress"
LABELS["status_in_review"]="status:in-review"
LABELS["status_done"]="status:done"

for resource_name in "${!LABELS[@]}"; do
  label_name="${LABELS[$resource_name]}"
  echo "Importing github_issue_label.${resource_name} <- ${label_name}"
  terraform import "github_issue_label.${resource_name}" "${REPO}:${label_name}" || \
    echo "  SKIP: ${label_name} ei löydy reposta (luodaan apply:ssä)"
done

echo ""
echo "Import valmis. Aja seuraavaksi: terraform plan"
```

---

## 8. Täydellinen workflow ennen ensimmäistä apply:tä

```bash
cd terraform/

# 1. Alusta
terraform init

# 2. GCP-resurssit (jos jo olemassa)
terraform import google_service_account.deploy \
  "projects/uutisseuranta-activitystreams/serviceAccounts/deploy-sa@uutisseuranta-activitystreams.iam.gserviceaccount.com"

terraform import google_artifact_registry_repository.docker \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/repositories/uutisseuranta"

# Cloud Run -palvelut (jos jo olemassa)
terraform import google_cloud_run_v2_service.query_api \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/query-api"
terraform import google_cloud_run_v2_service.write_api \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/write-api"
terraform import google_cloud_run_v2_service.og_scraper \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/services/og-scraper"

# Cloud Run -jobit (jos jo olemassa)
terraform import google_cloud_run_v2_job.rss_fetch \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/rss-fetch-job"
terraform import google_cloud_run_v2_job.voikko \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/voikko-job"
terraform import google_cloud_run_v2_job.og_enrichment \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/og-enrichment-job"
terraform import google_cloud_run_v2_job.likes_and_updated \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/likes-and-updated-job"

# Scheduler (jos jo olemassa)
terraform import google_cloud_scheduler_job.rss_pipeline \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/rss-pipeline"
terraform import google_cloud_scheduler_job.og_enrichment_schedule \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/og-enrichment-schedule"
terraform import google_cloud_scheduler_job.likes_and_updated_schedule \
  "projects/uutisseuranta-activitystreams/locations/europe-north1/jobs/likes-and-updated-schedule"

# 3. GitHub
terraform import github_branch_protection.main "bq-activitystreams:main"
bash import_labels.sh

# 4. Tarkista
terraform plan

# 5. Aja
terraform apply
```

---

## 9. Virhetilanteet

### `Error: googleapi: Error 409: Resource already exists`
Resurssi on GCP:ssä mutta ei Terraform state:ssa. Aja kyseinen import-komento.

### `Error: Error acquiring the state lock`
Jos käytät GCS-backendia ja edellinen ajo jumi. Poista lukko:
```bash
terraform force-unlock <LOCK_ID>
```

### `Error: Service account does not exist`
Palvelutili on poistettu tai projekti on väärä. Tarkista `var.gcp_project`.

### Cloud Run 404 importissa
Palvelu ei ole olemassa. Terraform luo sen `apply`:ssä. Ei tarvita importia.

### `Error: Permission denied on Cloud Run`
Palvelutilillä pitää olla `roles/run.admin` sekä `roles/iam.serviceAccountUser`.
