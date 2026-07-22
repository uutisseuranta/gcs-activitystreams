# terraform/main.tf
# Juuritason Terraform-konfiguraatio bq-activitystreams-projektille.
#
# Tämä tiedosto korvaa:
#   deploy/init-sa.sh     → google_service_account + google_project_iam_member
#   deploy/deploy.sh      → cloud_run_v2_service / cloud_run_v2_job -resurssit
#   deploy/*.env.yaml     → env-lohkot kussakin Cloud Run -resurssissa
#
# Issue-viittaukset:
#   #28  Security Hardening – branch protection, WIF, CORS
#   #52  CSP-otsakkeet (toteutetaan frontendin puolella, tässä IAM-pohja)

terraform {
  required_version = ">= 1.7.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }

  # Vaihda bucket ja prefix omaan GCS-backendiin.
  # backend "gcs" {
  #   bucket = "uutisseuranta-activitystreams-tfstate"
  #   prefix = "terraform/state"
  # }
}

provider "google" {
  project = var.gcp_project
  region  = var.region
}

provider "github" {
  owner = "uutisseuranta"
  # token luetaan GITHUB_TOKEN-ympäristömuuttujasta
}
