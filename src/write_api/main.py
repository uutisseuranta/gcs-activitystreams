# src/write_api/main.py
#
# ActivityStreams Write API – HTTP-rajapinta aktiviteettien vastaanottamiseen
#
# Vastaa:
#   - Like, Create (Note), Delete, Update -aktiviteettien vastaanottamisesta
#   - Google OIDC JWT -autentikoinnista (loppukäyttäjä + service-to-service)
#   - Aktiviteettien tallentamisesta kahteen BigQuery-datasetiin:
#       {BQ_DATASET}.objects         → julkinen artikkelidata
#       {BQ_SOCIAL_DATASET}.activities → sosiaalinen lokitaulu (append-only)
#       {BQ_SOCIAL_DATASET}.likes    → tykkayslookup (duplikaattisuojaus)
#
# Arkkitehtuuriraja:
#   Kaikki kirjoitukset kahdelle BQ-datasetille tapahtuvat tässä tiedostossa.
#   Query-API (query_api/main.py) on read-only — se ei kirjoita koskaan.
#   og-scraper (og_scraper/) kirjoittaa vain {BQ_DATASET}.objects-tauluun.
#
# Ympäristömuuttujat (pakolliset):
#   GCP_PROJECT, BQ_DATASET, GOOGLE_CLIENT_ID
# Ympäristömuuttujat (valinnaiset):
#   BQ_SOCIAL_DATASET (oletus: activitystreams_social)
#   CLOUD_RUN_SERVICE_URL (service-to-service audience)
#   ALLOW_MOCK_AUTH     (vain testi/dev, ei koskaan tuotannossa)
import datetime
import json
import logging
import os
from typing import Any, Dict, Optional

import ulid
from fastapi import FastAPI, Header, HTTPException, Response
from google.auth.transport import requests as google_requests
from google.cloud import bigquery
from google.oauth2 import id_token


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
logger = logging.getLogger("write-api")

app = FastAPI(title="ActivityStreams Write API", version="1.0.0")

# Ympäristömuuttujat
PROJECT = os.getenv("GCP_PROJECT")
DATASET = os.getenv("BQ_DATASET")
SOCIAL_DATASET = os.getenv("BQ_SOCIAL_DATASET", "activitystreams_social")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLOUD_RUN_SERVICE_URL = os.getenv("CLOUD_RUN_SERVICE_URL", "")
ALLOW_MOCK_AUTH = os.getenv("ALLOW_MOCK_AUTH", "false").lower() == "true"

# Tuetut audience-arvot: loppukäyttäjän OAuth client ID + service-to-service URL
ALLOWED_AUDIENCES = [a for a in [GOOGLE_CLIENT_ID, CLOUD_RUN_SERVICE_URL] if a]

if not PROJECT or not DATASET:
    logger.critical("Virhe: GCP_PROJECT ja BQ_DATASET ovat pakollisia ympäristömuuttujia.")

bq_client = bigquery.Client(project=PROJECT)


def verify_auth_token(auth_header: Optional[str]) -> str:
    """Validoi Google OIDC JWT-tokenin ja palauttaa käyttäjän sub-tunnisteen.

    Tukee kahta audience-arvoa (confused deputy -suojaus):
    - GOOGLE_CLIENT_ID: loppukäyttäjän Sign in with Google -tokeni
    - CLOUD_RUN_SERVICE_URL: service-to-service OIDC-tokeni (Cloud Scheduler, jobit)
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning("Autentikaatio hyjätty: Authorization-otsake puuttuu tai virheellinen")
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")

    token = auth_header.split(" ")[1]

    # Mock-token sallittu vain kun ALLOW_MOCK_AUTH=true (ei koskaan tuotannossa)
    if ALLOW_MOCK_AUTH and token == "mock-test":
        logger.warning("Mock-autentikaatio käytössä — vain kehitysympäristöön")
        return "test-user-sub-12345"

    # Yritetään validoida tokeni jokaisella tuetulla audience-arvolla
    last_error = None
    for audience in ALLOWED_AUDIENCES:
        try:
            id_info = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                audience=audience
            )

            if not id_info.get("email_verified"):
                logger.warning(f"Autentikaatio hyjätty: email_verified=false, sub={id_info.get('sub')}")
                raise HTTPException(status_code=403, detail="Email domain must be verified.")

            logger.info(f"Autentikaatio onnistui: sub={id_info['sub']}, aud={audience}")
            return id_info["sub"]
        except HTTPException:
            raise
        except Exception as e:
            last_error = e
            continue

    logger.warning(f"Autentikaatio hyjätty: tokeni ei kelpaa millekaan audience-arvolle. Virhe: {last_error}")
    raise HTTPException(status_code=401, detail="Authentication failed.")


def get_object_by_id(obj_id: str) -> Optional[Dict[str, Any]]:
    """Hakee objektin julkisesta objects-taulusta id:n perusteella.

    BQ-kustannusriski: kysely käyttää parametrisoitua WHERE id = @id -ehtoa.
    objects-taulussa id on klusteroitu sarake (CLUSTER BY id) — BigQuery
    rajaa luettavat lohkot automaattisesti eikä tee full-table scania.
    Jos klusterointi poistetaan, tämä kysely muuttuu full-table scaniksi.
    Katso tiketti #29 ennen rakennemuutoksia.
    """
    query = f"""
        SELECT id, source, object_json, deleted, like_count
        FROM `{PROJECT}.{DATASET}.objects`
        WHERE id = @id LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", obj_id)]
    )
    try:
        rows = list(bq_client.query(query, job_config=job_config).result())
        if rows:
            row = rows[0]
            obj = json.loads(row["object_json"]) if isinstance(row["object_json"], str) else row["object_json"]
            return {
                "id": row["id"],
                "source": row["source"],
                "deleted": row["deleted"],
                "like_count": row["like_count"],
                "object_json": obj
            }
    except Exception as e:
        logger.error(f"Virhe haettaessa objektia {obj_id}: {e}")
    return None


def get_comments_count_by_actor(actor: str, object_id: str) -> int:
    """Laskee kuinka monta aktiviteettia tietty actor on tehnyt kyseiseen kohteeseen.

    BQ-kustannusriski: activities-taulussa ei ole klusterointia actor-sarakkeelle.
    Tämä kysely voi tehdä full-table scanin suurella datalla.
    Harkitse klusterointia (actor, object_id) jos aktiviteettimäärä kasvaa yli 10M riviin.
    Katso tiketti #29.
    """
    query = f"""
        SELECT COUNT(*) AS c
        FROM `{PROJECT}.{SOCIAL_DATASET}.activities`
        WHERE actor = @actor AND object_id = @object_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("actor", "STRING", actor),
            bigquery.ScalarQueryParameter("object_id", "STRING", object_id)
        ]
    )
    try:
        rows = list(bq_client.query(query, job_config=job_config).result())
        return rows[0]["c"] if rows else 0
    except Exception as e:
        logger.error(f"Virhe lasketaessa kommenttien määrää: {e}")
    return 0


def check_like_exists(actor: str, object_id: str) -> bool:
    """Tarkistaa onko käyttäjä jo tykännyt kohteesta sosiaalisessa kannassa.

    BQ-kustannusriski: likes-taulussa WHERE actor + object_id -ehto.
    Taulu on klusteroitu (object_id, actor) — BigQuery rajaa lohkot.
    LIMIT 1 varmistaa että skannaus pysähtyy heti ensimmäiseen löydökseen.
    Katso tiketti #29.
    """
    query = f"""
        SELECT 1
        FROM `{PROJECT}.{SOCIAL_DATASET}.likes`
        WHERE actor = @actor AND object_id = @object_id LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("actor", "STRING", actor),
            bigquery.ScalarQueryParameter("object_id", "STRING", object_id)
        ]
    )
    try:
        rows = list(bq_client.query(query, job_config=job_config).result())
        return len(rows) > 0
    except Exception as e:
        logger.error(f"Virhe tykkäystarkistuksessa: {e}")
    return False


@app.post("/ap/activities")
def post_activity(activity: Dict[str, Any], authorization: Optional[str] = Header(None)):
    # 1. Autentikoidaan pyynto
    sub = verify_auth_token(authorization)
    actor_id = f"https://activitystreams.uutisseuranta.net/ap/users/{sub}"

    # Korvataan pyynnön actor meidän generoimallamme turvallisella Google actor-id:llä
    activity["actor"] = actor_id

    act_type = activity.get("type")
    act_object = activity.get("object")

    if not act_type or not act_object:
        raise HTTPException(status_code=400, detail="Missing 'type' or 'object' in activity.")

    published_str = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    # 2. Reititetään aktiviteetin mukaan
    if act_type == "Like":
        obj_id = act_object if isinstance(act_object, str) else act_object.get("id")
        if not obj_id:
            raise HTTPException(status_code=400, detail="Missing object ID for Like.")

        # Varmistetaan että kohde on olemassa
        target_obj = get_object_by_id(obj_id)
        if not target_obj or target_obj["deleted"]:
            raise HTTPException(status_code=404, detail="Target object not found or deleted.")

        # Duplikaattisuojaus
        if check_like_exists(actor_id, obj_id):
            logger.info(f"Käyttäjä {actor_id} on jo tykännyt kohteesta {obj_id}. Ohitetaan hiljaa.")
            return {"status": "already_liked", "id": activity.get("id")}

        # Luodaan Like-aktiviteetille uniikki id
        act_id = f"https://activitystreams.uutisseuranta.net/ap/activities/likes/{ulid.new().str}"
        activity["id"] = act_id
        activity["published"] = published_str

        # Kirjoitetaan activities ja likes -tauluihin sosiaalisessa datasetissä
        activities_row = {
            "id": act_id,
            "type": "Like",
            "actor": actor_id,
            "object_id": obj_id,
            "object_type": target_obj["object_json"].get("type", "Article"),
            "object_json": json.dumps(activity),
            "target_id": None,
            "in_reply_to": None,
            "thread_root": None,
            "published": published_str,
            "received_at": published_str
        }

        likes_row = {
            "activity_id": act_id,
            "actor": actor_id,
            "object_id": obj_id,
            "published": published_str
        }

        # Tallennetaan BigQuery stream-insertillä
        try:
            bq_client.insert_rows_json(f"{PROJECT}.{SOCIAL_DATASET}.activities", [activities_row])
            bq_client.insert_rows_json(f"{PROJECT}.{SOCIAL_DATASET}.likes", [likes_row])
        except Exception as e:
            logger.error(f"Virhe tallennettaessa tykkäystä BigQueryyn: {e}")
            raise HTTPException(status_code=500, detail="Database write failed.")

        return Response(status_code=201, content=json.dumps({"id": act_id}), media_type="application/json")

    elif act_type == "Create":
        if not isinstance(act_object, dict):
            raise HTTPException(status_code=400, detail="Object must be a valid JSON object.")

        obj_type = act_object.get("type")
        in_reply_to = act_object.get("inReplyTo")

        if obj_type != "Note":
            raise HTTPException(status_code=400, detail="Only 'Note' type objects are supported for Create.")

        if not in_reply_to:
            raise HTTPException(status_code=400, detail="'inReplyTo' is required for comment creation.")

        # Tarkistetaan ketjun syvyys ja haetaan thread_root
        parent = get_object_by_id(in_reply_to)
        if not parent or parent["deleted"]:
            raise HTTPException(status_code=404, detail="Parent object not found or deleted.")

        parent_type = parent["object_json"].get("type")

        thread_root = None
        if parent_type == "Article":
            # Taso 1 kommentti
            thread_root = parent["id"]
        elif parent_type == "Note":
            # Taso 2 vastaus kommenttiin.
            # Varmistetaan ettei parent itse ole vastaus vastaukseen.
            parent_in_reply_to = parent["object_json"].get("inReplyTo")
            grandparent = get_object_by_id(parent_in_reply_to) if parent_in_reply_to else None

            if grandparent and grandparent["object_json"].get("type") == "Note":
                # Tämä olisi taso 3, hyjätään!
                raise HTTPException(status_code=400, detail="Reply thread depth limit exceeded (max 2 levels).")

            # thread_root saadaan parentin thread_rootista
            thread_root = parent["object_json"].get("thread_root") or parent_in_reply_to
        else:
            raise HTTPException(status_code=400, detail="Unsupported parent object type.")

        # Luodaan Note-objektille ULID-id
        obj_id = f"https://activitystreams.uutisseuranta.net/ap/objects/comments/{ulid.new().str}"
        act_object["id"] = obj_id
        act_object["published"] = published_str
        act_object["attributedTo"] = actor_id
        act_object["thread_root"] = thread_root  # Tallennetaan dynaaminen thread_root

        # Aktiviteetin id
        act_id = f"https://activitystreams.uutisseuranta.net/ap/activities/creates/{ulid.new().str}"
        activity["id"] = act_id
        activity["object"] = act_object
        activity["published"] = published_str

        # 1. Kirjoitetaan activities sosiaaliseen datasetiin
        activities_row = {
            "id": act_id,
            "type": "Create",
            "actor": actor_id,
            "object_id": obj_id,
            "object_type": "Note",
            "object_json": json.dumps(activity),
            "target_id": None,
            "in_reply_to": in_reply_to,
            "thread_root": thread_root,
            "published": published_str,
            "received_at": published_str
        }

        # 2. Kirjoitetaan objects julkiseen datasetiin MERGE:llä
        merge_query = f"""
            MERGE `{PROJECT}.{DATASET}.objects` T
            USING (SELECT @id AS id, @object_json AS object_json, @published AS published) S
            ON T.id = S.id
            WHEN MATCHED THEN UPDATE SET
              T.object_json = PARSE_JSON(S.object_json),
              T.updated = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT
              (id, source, published, updated, tags, tags_enriched, like_count, deleted, object_json)
              VALUES (S.id, 'user', S.published, CURRENT_TIMESTAMP(), [], FALSE, 0, FALSE, PARSE_JSON(S.object_json));
        """
        merge_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("id", "STRING", obj_id),
                bigquery.ScalarQueryParameter("object_json", "STRING", json.dumps(act_object)),
                bigquery.ScalarQueryParameter("published", "TIMESTAMP", datetime.datetime.now(datetime.timezone.utc))
            ]
        )

        try:
            # Kirjoitetaan event logiin
            bq_client.insert_rows_json(f"{PROJECT}.{SOCIAL_DATASET}.activities", [activities_row])
            # Päivitetään julkinen objects-taulu
            bq_client.query(merge_query, job_config=merge_config).result()
        except Exception as e:
            logger.error(f"Virhe kommentin tallennuksessa: {e}")
            raise HTTPException(status_code=500, detail="Database write failed.")

        return Response(status_code=201, content=json.dumps({"id": act_id, "object_id": obj_id}), media_type="application/json")

    elif act_type == "Delete":
        obj_id = act_object if isinstance(act_object, str) else act_object.get("id")
        if not obj_id:
            raise HTTPException(status_code=400, detail="Missing object ID for Delete.")

        # Varmistetaan että poistettava objekti on olemassa ja haetaan sen omistaja
        target_obj = get_object_by_id(obj_id)
        if not target_obj:
            raise HTTPException(status_code=404, detail="Object not found.")

        if target_obj["deleted"]:
            return {"status": "already_deleted", "id": obj_id}

        # Tarkistetaan että poistaja on alkuperäinen luoja (attributedTo)
        owner_id = target_obj["object_json"].get("attributedTo")
        if owner_id != actor_id:
            raise HTTPException(status_code=403, detail="You do not have permission to delete this object.")

        act_id = f"https://activitystreams.uutisseuranta.net/ap/activities/deletes/{ulid.new().str}"
        activity["id"] = act_id
        activity["published"] = published_str

        # 1. Kirjoitetaan delete event logiin sosiaalisessa BQ:ssa
        activities_row = {
            "id": act_id,
            "type": "Delete",
            "actor": actor_id,
            "object_id": obj_id,
            "object_type": target_obj["object_json"].get("type", "Note"),
            "object_json": json.dumps(activity),
            "target_id": None,
            "in_reply_to": None,
            "thread_root": None,
            "published": published_str,
            "received_at": published_str
        }

        # 2. Päivitetään julkinen objects-taulu (deleted = TRUE)
        update_query = f"""
            UPDATE `{PROJECT}.{DATASET}.objects`
            SET deleted = TRUE, updated = CURRENT_TIMESTAMP()
            WHERE id = @id
        """
        update_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", obj_id)]
        )

        try:
            bq_client.insert_rows_json(f"{PROJECT}.{SOCIAL_DATASET}.activities", [activities_row])
            bq_client.query(update_query, job_config=update_config).result()
        except Exception as e:
            logger.error(f"Virhe objektin poistamisessa BigQuerystä: {e}")
            raise HTTPException(status_code=500, detail="Database write failed.")

        return {"status": "deleted", "id": act_id}

    elif act_type == "Update":
        if not isinstance(act_object, dict):
            raise HTTPException(status_code=400, detail="Object must be a valid JSON object.")

        obj_id = act_object.get("id")
        if not obj_id:
            raise HTTPException(status_code=400, detail="Missing object ID for Update.")

        # Varmistetaan olemassaolo ja oikeudet
        target_obj = get_object_by_id(obj_id)
        if not target_obj or target_obj["deleted"]:
            raise HTTPException(status_code=404, detail="Object not found or deleted.")

        owner_id = target_obj["object_json"].get("attributedTo")
        if owner_id != actor_id:
            raise HTTPException(status_code=403, detail="You do not have permission to update this object.")

        act_id = f"https://activitystreams.uutisseuranta.net/ap/activities/updates/{ulid.new().str}"
        activity["id"] = act_id
        activity["published"] = published_str

        # Yhdistetään uusi sisältö ja pidetään alkuperäiset kriittiset sarakkeet muuttumattomina
        updated_object = target_obj["object_json"].copy()
        updated_object["name"] = act_object.get("name", updated_object.get("name"))
        updated_object["summary"] = act_object.get("summary", updated_object.get("summary"))
        updated_object["content"] = act_object.get("content", updated_object.get("content"))
        updated_object["updated"] = published_str

        activities_row = {
            "id": act_id,
            "type": "Update",
            "actor": actor_id,
            "object_id": obj_id,
            "object_type": "Note",
            "object_json": json.dumps(activity),
            "target_id": None,
            "in_reply_to": None,
            "thread_root": None,
            "published": published_str,
            "received_at": published_str
        }

        # Päivitetään julkinen objects-taulu MERGE:llä
        merge_query = f"""
            MERGE `{PROJECT}.{DATASET}.objects` T
            USING (SELECT @id AS id, @object_json AS object_json) S
            ON T.id = S.id
            WHEN MATCHED THEN UPDATE SET
              T.object_json = PARSE_JSON(S.object_json),
              T.updated = CURRENT_TIMESTAMP()
        """
        merge_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("id", "STRING", obj_id),
                bigquery.ScalarQueryParameter("object_json", "STRING", json.dumps(updated_object))
            ]
        )

        try:
            bq_client.insert_rows_json(f"{PROJECT}.{SOCIAL_DATASET}.activities", [activities_row])
            bq_client.query(merge_query, job_config=merge_config).result()
        except Exception as e:
            logger.error(f"Virhe päivitettäessä kommenttia: {e}")
            raise HTTPException(status_code=500, detail="Database write failed.")

        return {"status": "updated", "id": act_id}

    else:
        raise HTTPException(status_code=400, detail=f"Activity type '{act_type}' is not supported.")


@app.get("/healthz")
def liveness():
    return {"status": "ok"}


@app.get("/readyz")
def readiness():
    try:
        # Kevät BigQuery-yhteyden tarkistus
        bq_client.list_datasets(max_results=1)
        return {"status": "ready"}
    except Exception as e:
        logger.error(f"Readiness-tarkistus epäonnistui: {e}")
        raise HTTPException(status_code=503, detail="Database connection failed")
