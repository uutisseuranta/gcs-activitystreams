# src/write_api/test_main.py
import os
import unittest
from unittest.mock import MagicMock, patch

# Asetetaan ympäristömuuttujat ennen FastAPI-appin importtaamista
os.environ["GCP_PROJECT"] = "test-project"
os.environ["BQ_DATASET"] = "test_dataset"
os.environ["BQ_SOCIAL_DATASET"] = "test_social_dataset"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["ALLOW_MOCK_AUTH"] = "true"

# Mockataan BigQuery-asiakas ennen main.py:n importtausta, jotta vältetään DefaultCredentialsError CI:ssä
import google.cloud.bigquery  # noqa: E402, I001
google.cloud.bigquery.Client = MagicMock()

from fastapi.testclient import TestClient  # noqa: E402, I001

from write_api.main import app  # noqa: E402, I001


class TestDeleteActivity(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("write_api.main.bq_client")
    def test_delete_success(self, mock_bq):
        # Setup mock for get_object_by_id
        mock_query_job = MagicMock()
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
            "source": "user",
            "deleted": False,
            "like_count": 0,
            "object_json": '{"id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X", "type": "Note", "attributedTo": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345"}'
        }
        mock_query_job.result.return_value = [mock_row]
        mock_bq.query.return_value = mock_query_job

        # Setup mock for insert_rows_json
        mock_bq.insert_rows_json.return_value = []

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X"
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)

        self.assertEqual(response.status_code, 200)
        resp_data = response.json()
        self.assertEqual(resp_data["status"], "deleted")
        self.assertTrue(resp_data["id"].startswith("https://activitystreams.uutisseuranta.net/ap/activities/deletes/"))

        # Varmistetaan että tallennukset tehtiin
        mock_bq.insert_rows_json.assert_called_once()
        self.assertEqual(mock_bq.query.call_count, 2)

    @patch("write_api.main.bq_client")
    def test_delete_404_not_found(self, mock_bq):
        # Mock get_object_by_id returns None
        mock_query_job = MagicMock()
        mock_query_job.result.return_value = []
        mock_bq.query.return_value = mock_query_job

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/nonexistent"
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 404)
        self.assertIn("Object not found", response.json()["detail"])

    @patch("write_api.main.bq_client")
    def test_delete_403_forbidden(self, mock_bq):
        # Luojan id (other-user) eroaa pyynnön tekijästä (test-user-sub-12345)
        mock_query_job = MagicMock()
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
            "source": "user",
            "deleted": False,
            "like_count": 0,
            "object_json": '{"id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X", "type": "Note", "attributedTo": "https://activitystreams.uutisseuranta.net/ap/users/other-user"}'
        }
        mock_query_job.result.return_value = [mock_row]
        mock_bq.query.return_value = mock_query_job

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X"
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 403)
        self.assertIn("permission to delete", response.json()["detail"])

    @patch("write_api.main.bq_client")
    def test_delete_idempotency(self, mock_bq):
        # Kohdeobjektin deleted on jo TRUE
        mock_query_job = MagicMock()
        mock_row = {
            "id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X",
            "source": "user",
            "deleted": True,
            "like_count": 0,
            "object_json": '{"id": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X", "type": "Note", "attributedTo": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345"}'
        }
        mock_query_job.result.return_value = [mock_row]
        mock_bq.query.return_value = mock_query_job

        headers = {"Authorization": "Bearer mock-test"}
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X"
        }

        response = self.client.post("/ap/activities", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "already_deleted")

    def test_delete_401_unauthorized(self):
        payload = {
            "type": "Delete",
            "actor": "https://activitystreams.uutisseuranta.net/ap/users/test-user-sub-12345",
            "object": "https://activitystreams.uutisseuranta.net/ap/objects/comments/01H7X"
        }
        response = self.client.post("/ap/activities", json=payload)
        self.assertEqual(response.status_code, 401)
