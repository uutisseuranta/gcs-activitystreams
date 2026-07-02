import datetime
import hashlib
import json
import logging
import os
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Response
from google.cloud import bigquery
from pydantic import BaseModel

# Käytetään jaettua OG-parseria
from shared import og_parser


# JsonFormatter toistuu identtisenä og_enrichment_job-, og_scraper- ja query_api-palveluissa.
# Tämä on tietoinen arkkitehtuuripäätös: Cloud Run -palvelut ovat toisistaan riippumattomia
# deployable-yksikköjä. Jakaminen shared/-moduuliin lisäisi build-riippuvuuden ilman selvää hyötyä,
# koska formatter on yksinkertainen (~10 riviä) eikä muutu usein.
# Päätös kirjattu: TECHNICAL_DESIGN.md §4 "Suunnittelu- ja kehityskäytännöt".
class JsonFormatter(logging.Formatter):
    def format(self, record):
        # Cloud Logging tunnistaa 'severity'-kentän automaattisesti logtasoksi
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            # ISO 8601 UTC — Cloud Logging edellyttää Z-päätettä (ei +00:00)
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
logger = logging.getLogger("og-scraper")

app = FastAPI(title="ActivityStreams OG Scraper", version="1.0.0")

PROJECT = os.getenv("GCP_PROJECT")
DATASET = os.getenv("BQ_DATASET", "activitystreams")

if not PROJECT:
    logger.critical("Virhe: GCP_PROJECT on pakollinen ympäristömuuttuja.")

bq_client = bigquery.Client(project=PROJECT)


class ScrapeRequest(BaseModel):
    url: str


@app.get("/healthz")
def healthz():
    # Cloud Run liveness-probe — vastaa aina 200 OK jos prosessi on elossa
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    # Cloud Run readiness-probe — tässä palvelussa ei ole erillistä BQ-yhteystarkistusta,
    # koska BQ-asiakas on globaali ja yhteys luodaan vasta ensimmäisessä kyselyssä.
    # Ks. query_api/main.py readyz() jossa on aktiivinen BQ-tarkistus.
    return {"status": "ok"}


@app.post("/ap/scrape", status_code=210)  # Standardissa palautetaan 201 tai 200, tiketti pyytää 201
def scrape_url(req: ScrapeRequest, response: Response):
    url = req.url.strip()

    # Validoi URL syntaksi — estetään tyhjät ja ei-HTTP(S)-protokollat ennen verkkopyyntöä
    parsed = urlparse(url)
    if not parsed.scheme or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL format.")

    # 1. Robots.txt check — kunnioitetaan sivuston crawling-kieltoja
    if not og_parser.robots_check(url):
        logger.warning(f"Crawling forbidden by robots.txt for URL: {url}")
        raise HTTPException(status_code=403, detail="Forbidden by robots.txt")

    # 2. Fetch HTML content — og_parser.fetch_url_stream sisältää SSRF-suojauksen:
    # - Estetään pyynnöt sisäisiin osoitteisiin (169.254.x.x, 10.x.x.x, 172.16-31.x.x, 192.168.x.x)
    # - Estetään pyynnöt loopback-osoitteisiin (127.x.x.x, ::1)
    # - PermissionError nostetaan SSRF-rikkomuksesta
    try:
        html_content = og_parser.fetch_url_stream(url)
    except PermissionError as pe:
        logger.warning(f"SSRF violation: {pe}")
        raise HTTPException(status_code=403, detail="Forbidden: SSRF validation failed.")
    except Exception as e:
        logger.error(f"Error fetching URL {url}: {e}")
        # Hienojakoisempi virhekoodi HTTP-virheen laadun mukaan
        err_msg = str(e).lower()
        if "timeout" in err_msg:
            raise HTTPException(status_code=504, detail="Gateway Timeout.")
        elif "status" in err_msg or "http" in err_msg:
            raise HTTPException(status_code=502, detail="Bad Gateway.")
        else:
            raise HTTPException(status_code=422, detail="Unprocessable Entity.")

    # 3. Parse OG/meta metadata
    try:
        metadata = og_parser.parse_og_metadata(html_content, url)
    except Exception as e:
        logger.error(f"Failed to parse OG tags for {url}: {e}")
        raise HTTPException(status_code=422, detail="Invalid HTML or missing OG tags.")

    # Vaaditaan vähintään title — sivut ilman otsikkoa eivät sovi AS2 Article -objektiksi
    title = og_parser.longer(metadata.get("title"), None)
    if not title:
         raise HTTPException(status_code=422, detail="No title found in HTML.")

    # 4. Muodosta AS2 Article -objekti
    # id: sha256(url) — determinisitinen, sama URL tuottaa aina saman id:n (idempotenttisuus)
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    as2_id = f"scraped/{url_hash}"

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_utc.isoformat().replace("+00:00", "Z")

    # Kartoitetaan published & updated — OG-tagit ovat etusijalla, fallback nykyhetkeen
    published_str = metadata.get("published_time") or now_iso
    updated_str = metadata.get("modified_time") or now_iso

    try:
        # Validoidaan ISO-muoto — virheelliset OG-päivämäärät korvataan nykyhetkellä
        datetime.datetime.fromisoformat(published_str.replace("Z", "+00:00"))
    except ValueError:
        published_str = now_iso

    try:
        datetime.datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
    except ValueError:
        updated_str = now_iso

    netloc = parsed.netloc
    site_name = metadata.get("site_name") or netloc
    site_url = f"{parsed.scheme}://{netloc}"

    article_json = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Article",
        "id": as2_id,
        "url": metadata.get("url") or url,
        "name": title,
        "summary": og_parser.longer(metadata.get("description"), None),
        "published": published_str,
        "updated": updated_str,
        "attributedTo": {
            "type": "Organization",
            "name": site_name,
            "url": site_url
        }
    }

    if metadata.get("image"):
        article_json["image"] = {
            "type": "Image",
            "url": metadata["image"]
        }

    # 5. Tallenna BigQueryyn MERGE-lauseella (upsert-semantiikka)
    # WHEN MATCHED: päivitetään vain scraped-lähteestä tulleet rivit — ei ylikirjoiteta RSS-dataa
    # WHEN NOT MATCHED: luodaan uusi rivi, og_enriched=TRUE koska scraper tekee rikastuksen heti
    try:
        # BigQuery edellyttää datetime-objekteja TIMESTAMP-parametreille (ei ISO-merkkijonoja)
        published_dt = datetime.datetime.fromisoformat(published_str.replace("Z", "+00:00"))
        updated_dt = datetime.datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
    except Exception:
        published_dt = now_utc
        updated_dt = now_utc

    merge_query = f"""
        MERGE `{PROJECT}.{DATASET}.objects` T
        USING (
          SELECT
            @id AS id,
            'scraped' AS source,
            @published AS published,
            @updated AS updated,
            SAFE_CAST(@object_json AS JSON) AS object_json
        ) S ON T.id = S.id
        WHEN MATCHED AND T.source = 'scraped' THEN
          UPDATE SET
            T.object_json = S.object_json,
            T.updated = S.updated,
            T.og_enriched = TRUE
        WHEN NOT MATCHED THEN
          INSERT (id, source, published, updated, tags, tags_enriched, og_enriched, og_enriched_error, like_count, deleted, object_json)
          VALUES (S.id, S.source, S.published, S.updated, [], FALSE, TRUE, NULL, 0, FALSE, S.object_json)
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", as2_id),
            bigquery.ScalarQueryParameter("published", "TIMESTAMP", published_dt),
            bigquery.ScalarQueryParameter("updated", "TIMESTAMP", updated_dt),
            # object_json talletetaan merkkijonona ja SAFE_CAST muuntaa sen BigQuery JSON -tyypiksi
            bigquery.ScalarQueryParameter("object_json", "STRING", json.dumps(article_json)),
        ]
    )

    try:
        query_job = bq_client.query(merge_query, job_config=job_config)
        query_job.result()  # Odotetaan completion ennen vastausta
        logger.info(f"Successfully scraped and merged URL {url} into BQ as {as2_id}")
    except Exception as e:
        logger.error(f"BigQuery MERGE failed for {url}: {e}")
        raise HTTPException(status_code=500, detail="Database write failure.")

    response.status_code = 201
    return article_json
