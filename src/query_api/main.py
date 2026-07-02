# src/query_api/main.py
import datetime
import json
import logging
import os
import time
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query, Response
from google.cloud import bigquery


# Lokitus asetukset
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
logger = logging.getLogger("query-api")

app = FastAPI(title="ActivityStreams Query API", version="1.0.0")

# Globaalit ympäristömuuttujat
PROJECT = os.getenv("GCP_PROJECT")
DATASET = os.getenv("BQ_DATASET")
LOCATION = os.getenv("BQ_LOCATION", "europe-north1")

if not PROJECT or not DATASET:
    logger.critical("Virhe: GCP_PROJECT ja BQ_DATASET ympäristömuuttujat ovat pakollisia.")

bq_client = bigquery.Client(project=PROJECT)

# totalItems cache: { "tag1,tag2": { "value": int, "expires": float } }
_count_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 300  # 5 minuuttia


def get_total_items_cached(tags: List[str]) -> int:
    """Laskee objektien kokonaismäärän välimuistia hyödyntäen."""
    now = time.time()
    # Lajitellaan tagit jotta järjestys ei vaikuta avaimeen
    cache_key = ",".join(sorted(tags))

    cached = _count_cache.get(cache_key)
    if cached and cached["expires"] > now:
        return cached["value"]

    # Lasketaan uusi arvo BigQuerystä
    query = f"""
        SELECT COUNT(*) AS c
        FROM `{PROJECT}.{DATASET}.objects`
        WHERE deleted = FALSE
          AND EXISTS (
            SELECT 1 FROM UNNEST(tags) t WHERE t IN UNNEST(@search_tags)
          )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("search_tags", "STRING", tags)]
    )

    try:
        results = list(bq_client.query(query, job_config=job_config).result())
        count = results[0]["c"] if results else 0
    except Exception as e:
        logger.error(f"Virhe laskettaessa totalItems-arvoa: {e}")
        # Palautetaan 0 tai vanha cache jos kysely epäonnistuu
        return cached["value"] if cached else 0

    # Päivitetään cache
    _count_cache[cache_key] = {
        "value": count,
        "expires": now + CACHE_TTL
    }
    return count


@app.get("/ap/outbox")
def get_outbox(
    tag: List[str] = Query(default=None, description="Haettavat tagit (toistuva parametri)"),
    n: int = Query(default=50, description="Palautettavien kohteiden määrä (1-500)")
):
    # 1. Validoidaan tagit
    if not tag:
        raise HTTPException(
            status_code=400,
            detail="At least one 'tag' query parameter is required."
        )

    # 2. Validoidaan n-parametri
    if n <= 0 or n > 500:
        raise HTTPException(
            status_code=400,
            detail="Parameter 'n' must be between 1 and 500."
        )

    # Siivotaan tagit (pieniksi kirjaimiksi ja välilyönnit pois)
    search_tags = [t.strip().lower() for t in tag if t.strip()]
    if not search_tags:
        raise HTTPException(
            status_code=400,
            detail="Valid tags must be provided."
        )

    logger.info(f"Haku tageilla: {search_tags}, koko n: {n}")

    # 3. Suoritetaan BigQuery-haku relevanssijärjestyksessä
    # Relevanssi lasketaan osuvien hakutagien lukumääränä.
    # Tasatilanteessa järjestetään: like_count DESC, updated DESC, published DESC, id ASC.
    query = f"""
        SELECT
          id,
          source,
          published,
          updated,
          like_count,
          object_json,
          (
            SELECT COUNT(*)
            FROM UNNEST(tags) t
            WHERE t IN UNNEST(@search_tags)
          ) AS relevance
        FROM `{PROJECT}.{DATASET}.objects`
        WHERE deleted = FALSE
          AND EXISTS (
            SELECT 1 FROM UNNEST(tags) t WHERE t IN UNNEST(@search_tags)
          )
        ORDER BY relevance DESC, like_count DESC, updated DESC, published DESC NULLS LAST, id ASC
        LIMIT @limit_n
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("search_tags", "STRING", search_tags),
            bigquery.ScalarQueryParameter("limit_n", "INT64", n)
        ]
    )

    try:
        query_job = bq_client.query(query, job_config=job_config)
        rows = list(query_job.result())
    except Exception as e:
        logger.error(f"BigQuery-haku epäonnistui: {e}")
        raise HTTPException(status_code=500, detail="Database query failed.")

    # 4. Dynaamisten kenttien (likes & updated) injektointi AS2-dokumentteihin
    ordered_items = []
    for row in rows:
        obj_json_raw = row["object_json"]
        if not obj_json_raw:
            continue

        try:
            # Muunnetaan JSON dictiksi jos tarpeen
            obj = json.loads(obj_json_raw) if isinstance(obj_json_raw, str) else obj_json_raw

            # Injektoidaan tykkäykset ja uusin päivitys
            obj["likes"] = row["like_count"]
            if row["updated"]:
                # Muutetaan ISO UTC -muotoon
                updated_dt = row["updated"]
                if isinstance(updated_dt, datetime.datetime):
                    obj["updated"] = updated_dt.isoformat().replace("+00:00", "Z")
                else:
                    obj["updated"] = str(updated_dt)

            ordered_items.append(obj)
        except Exception as e:
            logger.error(f"Virhe objektin {row['id']} parsimisessa: {e}")

    # 5. Lasketaan totalItems (cachettuna)
    total_items = get_total_items_cached(search_tags)

    # 6. Rakennetaan palautus-URL
    base_url = "https://activitystreams.uutisseuranta.net/ap/outbox"
    tag_params = "&".join(f"tag={t}" for t in search_tags)
    self_url = f"{base_url}?{tag_params}&n={n}"

    response_json = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "OrderedCollection",
        "id": self_url,
        "totalItems": total_items,
        "orderedItems": ordered_items
    }

    # Palautetaan ActivityPub-yhteensopivalla Content-Typellä
    return Response(
        content=json.dumps(response_json, ensure_ascii=False),
        media_type="application/activity+json; charset=utf-8"
    )


@app.get("/healthz")
def liveness():
    return {"status": "ok"}


@app.get("/readyz")
def readiness():
    try:
        # Kevyt BigQuery-yhteyden tarkistus
        bq_client.list_datasets(max_results=1)
        return {"status": "ready"}
    except Exception as e:
        logger.error(f"Readiness-tarkistus epäonnistui: {e}")
        raise HTTPException(status_code=503, detail="Database connection failed")
