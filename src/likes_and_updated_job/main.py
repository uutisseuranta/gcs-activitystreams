# src/likes_and_updated_job/main.py
import datetime
import json
import logging
import os
import sys

from google.cloud import bigquery


# Lokitus
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
logger = logging.getLogger("likes-and-updated-job")


def main() -> None:
    # Luetaan ympäristömuuttujat
    project = os.getenv("GCP_PROJECT")
    dataset = os.getenv("BQ_DATASET")
    social_dataset = os.getenv("BQ_SOCIAL_DATASET", "activitystreams_social")

    if not project or not dataset:
        logger.critical("Virhe: GCP_PROJECT ja BQ_DATASET ovat pakollisia ympäristömuuttujia.")
        sys.exit(1)

    logger.info(f"Käynnistetään synkronointi. Projekti: {project}, Julkinen dataset: {dataset}, Sosiaalinen dataset: {social_dataset}")

    bq_client = bigquery.Client(project=project)

    # VAIHE 1: Tykkäyslaskennan päivitys julkiseen objects-tauluun
    # Laskee kunkin kohteen uniikit tykkäykset likes-taulusta ja päivittää objects.like_count
    # Estää poistettujen objektien (deleted = TRUE) laskurin päivityksen
    likes_merge_query = f"""
        MERGE `{project}.{dataset}.objects` T
        USING (
          SELECT object_id, COUNT(*) AS cnt
          FROM `{project}.{social_dataset}.likes`
          GROUP BY object_id
        ) S ON T.id = S.object_id
        WHEN MATCHED AND T.deleted = FALSE THEN
          UPDATE SET T.like_count = S.cnt
    """

    # VAIHE 2: updated-aikaleiman päivitys thread_root-artikkeleille
    # Etsii uusimmat aktiviteettiajat received_at (Like ja Create) per ketjun juuri (thread_root)
    # Suodattaa pois poistetut kommentit activities.Delete mukaisesti
    # Varmistaa ettei updated-aika koskaan kulje taaksepäin.
    updated_merge_query = f"""
        MERGE `{project}.{dataset}.objects` T
        USING (
          SELECT COALESCE(thread_root, object_id) AS root_url,
                 MAX(received_at) AS last_activity_at
          FROM `{project}.{social_dataset}.activities` a
          WHERE type IN ('Like', 'Create')
            AND NOT EXISTS (
              SELECT 1 FROM `{project}.{social_dataset}.activities` d
              WHERE d.type = 'Delete' AND d.object_id = a.object_id
            )
          GROUP BY root_url
        ) S ON T.id = S.root_url
        WHEN MATCHED AND S.last_activity_at > T.updated THEN
          UPDATE SET T.updated = S.last_activity_at
    """

    try:
        # 1. Ajetetaan Vaihe 1
        logger.info("Ajetaan Vaihe 1: Tykkäyslaskurin päivitys (likes -> objects.like_count)...")
        likes_job = bq_client.query(likes_merge_query)
        likes_job.result()
        logger.info("Tykkäyslaskurin päivitys suoritettu onnistuneesti.")

        # 2. Ajetetaan Vaihe 2
        logger.info("Ajetaan Vaihe 2: Aktiivisuus-aikaleimojen päivitys (activities -> objects.updated)...")
        updated_job = bq_client.query(updated_merge_query)
        updated_job.result()
        logger.info("Aktiivisuus-aikaleimojen päivitys suoritettu onnistuneesti.")

        logger.info("Kaikki synkronointivaiheet suoritettu onnistuneesti loppuun.")

    except Exception as e:
        logger.critical(f"Synkronointi epäonnistui tietokantavirheen vuoksi: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
