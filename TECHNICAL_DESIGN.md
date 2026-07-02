# Technical Design: gcs-activitystreams

Tämä dokumentti kokoaa kaikkien tikettien arkkitehtuuripäätökset yhdeksi luettavaksi kokonaisuudeksi. Arkkitehtuuriperiaatteet ovat [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) -tiedostossa.

---

## Yleiskuva

```
https://activitystreams.uutisseuranta.net/
  └── GET /ap/outbox?tag=asuminen&n=50  ← Cloud Run (query-api) → BigQuery
  └── POST /ap/scrape { url }           ← Cloud Run (og-scraper) → BigQuery
  └── POST /ap/activities               ← Cloud Run (write-api) → BigQuery

Kirjoittajat per taulu:

  activitystreams.objects          ← jobit #2 (RSS), #3 (Ahjo), #4 (HRI), #8 (OG-scraper), kirjoituspalvelu #7 (kommentit)
                                      suora MERGE-kirjoitus, tavallinen taulu
                                      tags-sarakkeen omistaja: Voikko-job (#6)
                                      like_count-sarakkeen omistaja: likes-and-updated-job (#11/#12)

  activitystreams_social.activities ← kirjoituspalvelu (#7): käyttäjätoiminnot
                                      (kommentit, tykkäykset, käyttäjän luomat objektit)
                                      append-only event log

  activitystreams_social.likes      ← kirjoituspalvelu (#7): Like-tapahtumat
  activitystreams.config            ← jobit päivittävät last_fetched_at ja dynaamiset URL:t
```

### GCP-konfiguraatio

| Muuttuja | Arvo |
|---|---|
| **Domain** | `activitystreams.uutisseuranta.net` |
| **GCP-projekti** | `uutisseuranta-activitystreams` |
| **Julkinen BigQuery dataset** | `activitystreams` (avoin data — luettavissa ilman autentikaatiota) |
| **Yksityinen BigQuery dataset** | `activitystreams_social` (sosiaalinen data — kirjoitus vaatii käyttäjän `id_token`in) |
| **Sijainti** | `europe-north1` |
| **GitHub-repo** | `uutisseuranta/gcs-activitystreams` |

### Cloud Scheduler -ajastukset

| Job | Cron | Kellonaika (EET) |
|---|---|---|
| `rss-fetch-job` | `0 * * * *` | kerran tunnissa |
| `og-enrichment-job` | `5 * * * *` | 5 min RSS-jobin jälkeen |
| `ahjo-fetch-job` | `0 3 * * *` | 06:00 |
| `hri-fetch-job` | `30 3 * * *` | 06:30 |
| `voikko-enrich-job` | `30 * * * *` | 30 min välein |
| `likes-and-updated-job` | `*/15 * * * *` | 15 min välein |

Huom: `likes-and-updated-job` korvaa aiemmat erilliset `likes-sync-job` ja `activity-updated-job` -ajastukset. Molemmat laskennat ajetaan samassa Cloud Run Job -suorituksessa — ks. [Tykkäyslaskuri ja updated-aikaleima](#tykkäyslaskuri-ja-updated-aikaleima-1112).

> [!NOTE]
> **Kesäaika:** Cloud Scheduler käyttää UTC:tä eikä säädä kesäaikaa automaattisesti. EET (talviaika UTC+2) ja EEST (kesäaika UTC+3) eroavat tunnin. Cron-kommentit on merkitty EET-ajassa — kesällä ajastukset lähtevät tunnin myöhemmin kuin kommentti ilmoittaa. Tämä on hyväksytty trade-off: ajastusajalla ei ole kriittistä merkitystä tässä kontekstissa.

---

## Autentikaatio ja valtuutus

> **Periaate:** `activitystreams`-datasetti on julkista avointa dataa — luku ei vaadi autentikaatiota. **Käyttäjälähtöiset kirjoitusoperaatiot** (`activitystreams_social`-datasetti) vaativat käyttäjän Google `id_token`-tokenin. Backend- ja jobiprosessit kirjoittavat `activitystreams`-avoimelle puolelle Cloud IAM -palvelutilioikeuksilla ilman käyttäjäkontekstia. Backend ei luota UI:n tilaan.

| Endpoint / Prosessi | Dataset | Cloud IAM | Sovellustason autentikaatio | Perustelu |
|---|---|---|---|---|
| `GET /ap/outbox` | `activitystreams` (luku) | `--allow-unauthenticated` | ❌ Ei | Julkinen avoin data |
| `POST /ap/scrape` | `activitystreams.objects` (kirjoitus) | `--no-allow-unauthenticated` | ❌ Ei käyttäjätokenia | Backend-prosessi: kirjoittaa palvelutilin IAM-oikeuksilla avoimelle puolelle — ei käyttäjäkontekstia |
| `POST /ap/activities` | `activitystreams_social` (kirjoitus) | `--no-allow-unauthenticated` | ✅ Käyttäjän `id_token` pakollinen | Sosiaalinen data — kirjoitetaan käyttäjän identiteetillä |

HTTP-statuskoodit autentikaatiovirheissä (`write-api`):

```
401 Unauthorized  — Authorization-otsake puuttuu tai token ei kelpaa
403 Forbidden     — token kelvollinen mutta ei oikeutta operaatioon
```

### Kirjoitusoperaatiot kahdessa luokassa

**Luokka 1 — Backend-prosessit (jobit + OG-scraper):**
Kirjoittavat `activitystreams`-avoimelle puolelle Cloud IAM -palvelutilioikeuksilla. Ei käyttäjäkontekstia. Näihin kuuluvat: `rss-fetch-job`, `ahjo-fetch-job`, `hri-fetch-job`, `voikko-enrich-job`, `og-enrichment-job`, `likes-and-updated-job`, sekä `og-scraper`-endpoint (`POST /ap/scrape`).

**Luokka 2 — Käyttäjälähtöiset operaatiot (write-api):**
Kirjoittavat `activitystreams_social`-yksityiselle puolelle käyttäjän Google `id_token`-tokenilla. Kaikki `POST /ap/activities`-kutsut kuuluvat tähän luokkaan.

### Gmail SSO ja kirjoituspalvelu (#7, #19)

Kirjoituspalvelu (#7) tukee loppukäyttäjien autentikointia Google-kirjautumisella (Gmail SSO, OIDC `id_token`). Cloud IAM -rooli `roles/run.invoker` säilyy **palvelutilien** (Cloud Scheduler, Cloud Run Jobit) kontrollimekanismina. Loppukäyttäjien valtuutus tapahtuu sovellustasolla `id_token`-validoinnilla — erillistä GCP IAM -roolia ei loppukäyttäjille luoda.

#### Autentikaatiovirta (käyttäjä → write-api)

```
Selain
  └▶ Google Sign-In (OAuth2 / OIDC)
        └▶ id_token (JWT: sub, email, email_verified)
              └▶ POST /ap/activities
                    Authorization: Bearer <id_token>
                        └▶ Cloud Run (write-api)
                              └▶ google-auth-library: verify_oauth2_token()
                                    └▶ email_verified = true?
                                          └▶ sub → actor-IRI → activitystreams_social
```

#### Token-validointilogiikka

```python
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

def verify_google_token(bearer_token: str) -> dict:
    """
    Palauttaa: { sub, email, email_verified }
    Heittää: ValueError jos token ei kelpaa
    """
    info = id_token.verify_oauth2_token(
        bearer_token,
        google_requests.Request(),
        audience=GOOGLE_CLIENT_ID  # env-muuttuja
    )
    if not info.get("email_verified"):
        raise ValueError("email_verified = false")
    return info

def actor_iri(sub: str) -> str:
    return f"https://activitystreams.uutisseuranta.net/ap/users/{sub}"
```

HTTP-statuskoodit autentikaatiovirheissä:

| Tilanne | Status |
|---|---|
| `Authorization`-otsake puuttuu | `401 Unauthorized` |
| Token vanhentunut tai väärä `aud` | `401 Unauthorized` |
| `email_verified = false` | `403 Forbidden` |
| `actor` ≠ tokenin `sub` (Update/Delete) | `403 Forbidden` |

#### Sovellustason rooli: activitystreams-writer

Validoitu Google-tili (`email_verified = true`) saa sovellustason roolin **activitystreams-writer**. Tämä ei ole GCP IAM -rooli vaan kirjoituspalvelun sisäinen konsepti.

| Aktiviteetti | Sallittu | Ehto |
|---|---|---|
| `Create` | ✅ | `email_verified = true` |
| `Like` | ✅ | `email_verified = true` |
| `Update` | ✅ | `email_verified = true` + `actor`-IRI:n `sub` vastaa tokenin `sub` |
| `Delete` | ✅ | `email_verified = true` + `actor`-IRI:n `sub` vastaa tokenin `sub` |
| `Dislike`, `Announce`, `Undo` | ❌ | `400 Bad Request` — ks. [Tuetut aktiviteetit](#tuetut-aktiviteetit) |

#### `actor`-IRI ja Google `sub`

Käyttäjän pysyvä identiteetti (AS2 `actor`) sidotaan Google-tilin `sub`-kenttään, joka on vakaa tunnus sähköpostiosoitteen mahdollisista muutoksista huolimatta:

```
actor = "https://activitystreams.uutisseuranta.net/ap/users/{google-sub}"
```

Käyttäjän luomien objektien `id`-kenttä sisältää edelleen erillisen `ulid`-tunnuksen (ks. [`id`-kentän kaava lähteittäin](#id-kentän-kaava-lähteittäin)), mutta omistajuustarkistus tehdään `actor`-IRI:n `sub`-osasta.

#### Ympäristömuuttujat (täydennys #16:een)

| Muuttuja | Arvo | Kuvaus |
|---|---|---|
| `GOOGLE_CLIENT_ID` | `<OAuth2-client-id>.apps.googleusercontent.com` | `id_token`-validointiin (`aud`-kenttä) |
| `ALLOWED_EMAIL_DOMAINS` | *(tyhjä = kaikki)* | Haluttaessa rajoitetaan pääsy tiettyihin sähköpostidomaineihin |

> [!NOTE]
> `CLOUD_RUN_SERVICE_URL`-muuttujaa **ei tarvita** tässä projektissa. `id_token` validoidaan `GOOGLE_CLIENT_ID`-muuttujaa vastaan (`aud`-kenttä), ei Cloud Run -palvelun URL:aa vastaan.

---

## BigQuery-skeema (#1)

### Datasettijako

| Dataset | Näkyvyys | Kirjoittaa | Lukee |
|---|---|---|---|
| `activitystreams` | Julkinen avoin data | Jobit (#2–#4, #6, #8, #24), write-api (#7, kommentit objects-tauluun) | query-api, kaikki |
| `activitystreams_social` | Yksityinen, token-suojattu | write-api (#7, käyttäjätoiminnot käyttäjän id_tokenilla) | likes-and-updated-job (laskee likes → objects) |

### `activitystreams.objects` — artikkelit, päätökset, datasetit

```sql
CREATE TABLE activitystreams.objects (
  id              STRING    NOT NULL OPTIONS(description='AS2 id – domain-pohjainen IRI, primääriavain'),
  source          STRING    NOT NULL OPTIONS(description='Lähde: rss | ahjo | hri | scraped | user'),
  published       TIMESTAMP NOT NULL OPTIONS(description='AS2 published – pakollinen, taulu on partitionoitu tämän mukaan'),
  updated         TIMESTAMP          OPTIONS(description='AS2 updated – päivittyy käyttäjäaktiivisuudesta (#12)'),
  tags            ARRAY<STRING>      OPTIONS(description='Lemmatisoidut tagit (Voikko #6)'),
  tags_enriched   BOOL      NOT NULL OPTIONS(description='TRUE kun Voikko-job on käsitellyt rivin'),
  og_enriched     BOOL               OPTIONS(description='TRUE kun OG-tagit on haettu (rss-rivit: #24, scraped-rivit: aina TRUE)'),
  og_enriched_error STRING           OPTIONS(description='Virheviesti jos OG-haku epäonnistui — NULL = ei virhettä'),
  like_count      INT64     NOT NULL OPTIONS(description='Tykkäysmäärä activitystreams_social.likes-taulusta'),
  deleted         BOOL      NOT NULL OPTIONS(description='Pehmeä poisto'),
  object_json     JSON               OPTIONS(description='Koko AS2-objekti natiivina JSON-tyypinä')
)
PARTITION BY DATE(published)
CLUSTER BY source, published;
```

> **Oletusarvot:** `tags_enriched=FALSE`, `og_enriched=FALSE`, `like_count=0`, `deleted=FALSE` asetetaan INSERT-lauseissa, ei DDL:ssä. BigQuery CREATE TABLE ei tue DEFAULT-lausekkeita.

> **Miksi `published` on NOT NULL?**
> Taulu on partitionoitu `DATE(published)`-sarakkeen mukaan. Ilman `published`-arvoa rivi ei partitionoidu oikein ja queryt hidastuvat merkittävästi. RSS-job ohittaa artikkelit joilta `<pubDate>` puuttuu (ks. #2). OG-scraper tallentaa `published = scrape-hetki` fallbackina. Poikkeustapaukset käsitellään erillisessä prosessissa (#14).

### `activitystreams_social.activities` — append-only event log

```sql
CREATE TABLE activitystreams_social.activities (
  id            STRING    NOT NULL,
  type          STRING    NOT NULL,  -- Create | Update | Delete | Add | Remove | Like
  actor         STRING    NOT NULL,
  object_id     STRING,
  object_type   STRING,
  object_json   JSON,
  target_id     STRING,
  in_reply_to   STRING,
  thread_root   STRING,
  published     TIMESTAMP NOT NULL,
  received_at   TIMESTAMP NOT NULL
)
PARTITION BY DATE(published)
CLUSTER BY type, actor;
```

### `activitystreams_social.likes` — tykkäykset

```sql
CREATE TABLE activitystreams_social.likes (
  activity_id   STRING    NOT NULL,
  actor         STRING    NOT NULL,
  object_id     STRING    NOT NULL,
  published     TIMESTAMP NOT NULL
)
PARTITION BY DATE(published)
CLUSTER BY object_id, actor;
```

### `activitystreams.config` — dynaaminen konfiguraatio

Config-taulu on kriittinen komponentti: RSS-, Ahjo- ja HRI-jobien fetch-ikkunan jatkuvuus riippuu siitä kokonaan. Ilman oikeaa `last_fetched_at`-arvoa job joko hakee kaksoiskappaleet tai jättää aukon dataan.

```sql
CREATE TABLE activitystreams.config (
  key           STRING    NOT NULL,
  value         STRING    NOT NULL,
  updated_at    TIMESTAMP NOT NULL,
  updated_by    STRING    OPTIONS(description='Cloud Run Job -palvelun nimi, esim. rss-fetch-job')
);
```

#### Kaikki avain-arvo-parit

| key | Arvomuoto | Kirjoittaa | Milloin |
|---|---|---|---|
| `rss.{source}.last_fetched_at` | ISO 8601 timestamp | `rss-fetch-job` | Onnistuneen ajon lopussa |
| `ahjo.last_fetched_at` | ISO 8601 timestamp | `ahjo-fetch-job` | Onnistuneen ajon lopussa |
| `hri.last_fetched_at` | ISO 8601 timestamp | `hri-fetch-job` | Onnistuneen ajon lopussa |
| `valtioneuvosto.rss_url` | URL-merkkijono | `rss-fetch-job` | Kun autodiscovery löytää uuden URL:n |

**Lukuoikeudet:** Kaikilla fetch-jobeilla on `roles/bigquery.dataViewer` config-tauluun.

**Kirjoitusoikeudet:** Jokainen job kirjoittaa vain omia avaimiaan. Oikeus: `roles/bigquery.dataEditor` (tai hienojakoisempi rivi-tason käytäntö tarvittaessa).

#### Cold start — mitä tapahtuu kun avain puuttuu

| Tilanne | Käyttäytyminen |
|---|---|
| `last_fetched_at` puuttuu (ensimmäinen ajo) | Job hakee kiinteältä fallback-aikaikkunalta (esim. `-24h`) ja kirjoittaa arvon config-tauluun onnistuneen ajon jälkeen |
| `valtioneuvosto.rss_url` puuttuu | `rss-fetch-job` ajaa autodiscoveryn, tallentaa löydetyn URL:n ja jatkaa normaalisti |
| config-taulu on kokonaan tyhjä | Kaikki jobit käyttävät omaa fallback-ikkunaansa — data ei katkea, mutta ensimmäinen ajo saattaa hakea päällekkäistä dataa |

Config-taulun rivejä ei koskaan poisteta — vain päivitetään (`MERGE UPDATE`). Tämä varmistaa että avain on aina olemassa toisen ajon jälkeen.

### `id`-kentän kaava lähteittäin

| Lähde | `id`-kaava |
|---|---|
| RSS | `https://activitystreams.uutisseuranta.net/ap/objects/articles/{source}/{sha256(url)}` |
| Ahjo | `https://activitystreams.uutisseuranta.net/ap/objects/decisions/helsinki/{register_id}` |
| HRI-datasetti | `https://activitystreams.uutisseuranta.net/ap/objects/hri/datasets/{ckan-uuid}` |
| HRI-kategoria | `https://activitystreams.uutisseuranta.net/ap/objects/hri/groups/{group-name}` |
| OG-scrapattu | `https://activitystreams.uutisseuranta.net/ap/objects/scraped/{sha256(url)}` |
| Käyttäjän luoma objekti | `https://activitystreams.uutisseuranta.net/ap/objects/comments/{ulid}` |
| Käyttäjän identiteetti (actor) | `https://activitystreams.uutisseuranta.net/ap/users/{google-sub}` |

### `source`-sarake vs. `source`-query-parametri

`source`-sarake **on** `objects`-taulun sisäinen kenttä — se kertoo mistä objekti on peräisin (rss, ahjo, hri, scraped, user). Se ei koskaan näy API:n query-parametreina. `GET /ap/outbox?source=ahjo` palauttaa `400 Bad Request`. Client suodattaa aina tageilla, ei lähteellä.

---

## `published` ja `updated` lähteittäin (#9)

| Lähde | `published` | `updated` |
|---|---|---|
| RSS-artikkeli | `<pubDate>` — pakollinen, puuttuva artikkeli ohitetaan | `<atom:updated>` jos saatavilla, muuten `published` |
| OpenAhjo-päätös | `latest_decision_date` | API:n `modified` jos muuttunut |
| HRI-datasetti | `metadata_created` | `metadata_modified` |
| OG-scrapattu | `article:published_time` OG-tagista | `article:modified_time`, fallback scrape-hetki |
| OG-scrapattu (ei pubDate) | Scrape-hetki (fallback) | Scrape-hetki |

`published` ei koskaan muutu objektin päivityksissä. `updated` päivittyy myös kun artikkeliin kohdistuu käyttäjäaktiivisuutta (kommentti, tykkäys) — ks. #12.

---

## Cloud Run Job: RSS-syötteet (#2)

Ajastus: `0 * * * *` (kerran tunnissa). Virheenkäsittely: ks. [Virheenkäsittely ja retry-logiikka](#virheenkäsittely-ja-retry-logiikka).

**Lähteet:**

| Lähde | RSS-URL |
|---|---|
| Helsingin Sanomat | `https://www.hs.fi/rss/tuoreimmat.xml` |
| Iltalehti | `https://www.iltalehti.fi/rss/uutiset.xml` |
| Ilta-Sanomat | `https://www.is.fi/rss/tuoreimmat.xml` |
| Kauppalehti | `https://feeds.kauppalehti.fi/rss/main` |
| MTV Uutiset | `https://www.mtvuutiset.fi/rss.xml` |
| Valtioneuvosto | autodiscovery → tallennetaan `config`-tauluun |

**pubDate-vaatimus:** `<pubDate>` on pakollinen. Artikkeli jolta se puuttuu tai jota ei voi parsia ohitetaan ja merkitään lokiin. Syötteet joilta `<pubDate>` puuttuu rakenteellisesti käsitellään erillisessä prosessissa — ks. #14.

**Autodiscovery-logiikka:** Koodi lukee autodiscovery-lähteiden URL:n aina `RSS_FEEDS`-listan `autodiscover_url`-kenttästä, ei kovakoodattuna. Tämä mahdollistaa uusien autodiscover-lähteiden lisäämisen pelkällä env-muuttujapäivityksellä ilman koodimuutoksia:

```python
def get_feed_url(feed: dict, bq_client) -> str | None:
    """
    Palauttaa RSS-URL:n: lukee config-taulusta jos autodiscover,
    muuten käyttää staattista URL:a.
    `feed`-dict on yksittäinen RSS_FEEDS-listan alkio.
    """
    if not feed.get("autodiscover"):
        return feed["url"]

    config_key = f"{feed['name']}.rss_url"  # esim. "valtioneuvosto.rss_url"
    autodiscover_url = feed["autodiscover_url"]  # luettu RSS_FEEDS-listasta, ei kovakoodattu

    # Yritetään ensin config-taulusta
    row = bq_client.query(
        "SELECT value FROM activitystreams.config WHERE key = @key LIMIT 1",
        job_config=QueryJobConfig(query_parameters=[
            ScalarQueryParameter("key", "STRING", config_key)
        ])
    ).result()
    for r in row:
        return r.value

    # Ei löydy → autodiscovery
    url = discover_feed_url(autodiscover_url)
    if url:
        bq_client.query("""
            MERGE activitystreams.config T
            USING (SELECT @key AS key, @url AS value) S ON T.key = S.key
            WHEN MATCHED THEN
                UPDATE SET T.value = S.value, T.updated_at = CURRENT_TIMESTAMP(), T.updated_by = 'rss-fetch-job'
            WHEN NOT MATCHED THEN
                INSERT (key, value, updated_at, updated_by)
                VALUES (S.key, S.value, CURRENT_TIMESTAMP(), 'rss-fetch-job')
        """,
        job_config=QueryJobConfig(query_parameters=[
            ScalarQueryParameter("key", "STRING", config_key),
            ScalarQueryParameter("url", "STRING", url)
        ])).result()
    return url


# Käyttö: iterointi RSS_FEEDS-listan yli
for feed in rss_feeds:  # rss_feeds = json.loads(os.environ["RSS_FEEDS"])
    url = get_feed_url(feed, bq_client)
    if url:
        process_feed(feed["name"], url)
```

**Tallennuslogiikka:** MERGE-operaatio `id`-kentän perusteella (ks. [`id`-kentän kaava](#id-kentän-kaava-lähteittäin)). `tags`-, `tags_enriched`- ja `like_count`-sarakkeet jätetään `WHEN MATCHED` -haaran ulkopuolelle — Voikko-job (#6) ja likes-and-updated-job (#11/#12) omistavat ne. `WHEN NOT MATCHED`: `tags = []`, `tags_enriched = FALSE`, `like_count = 0`.

```sql
MERGE activitystreams.objects T
USING activitystreams.objects_temp S ON T.id = S.id
WHEN MATCHED AND S.updated > T.updated THEN
    UPDATE SET
        T.object_json = S.object_json,
        T.published   = S.published,
        T.updated     = S.updated,
        T.source      = S.source
        -- tags, tags_enriched, og_enriched, like_count ja deleted jätetään tarkoituksella pois
WHEN NOT MATCHED THEN
    INSERT (id, source, published, updated, tags, tags_enriched, og_enriched, like_count, deleted, object_json)
    VALUES (S.id, S.source, S.published, S.updated, [], FALSE, FALSE, 0, FALSE, S.object_json)
```

**Kuva-prioriteetti:** (1) `<media:thumbnail>`, (2) `<enclosure type="image/*">`, (3) `<image>` kanavan tasolla.

---

## Cloud Run Job: OpenAhjo-päätökset (#3)

Ajastus: `0 3 * * *` (06:00 EET). Virheenkäsittely: ks. [Virheenkäsittely ja retry-logiikka](#virheenkäsittely-ja-retry-logiikka). Base URL: `http://dev.hel.fi/openahjo/v1`.

**Fetch-ikkuna:** Ei kiinteää `-24h`. Käytetään `config`-taulun `ahjo.last_fetched_at`-arvoa — päivitetään vasta onnistuneen ajon jälkeen.

**Kenttäkartoitus:**

| AS2-kenttä | OpenAhjo-kenttä | Huomio |
|---|---|---|
| `id` | `register_id` (esim. `HEL-2026-012345`) | Muunnetaan IRI-muotoon: `…/decisions/helsinki/{register_id}` |
| `name` | `subject` | |
| `summary` | `agenda_item.content` (resolution) | |
| `published` | `latest_decision_date` | |
| `attributedTo.name` | `policymaker.name` | |

---

## Cloud Run Job: HRI-datasetit (#4)

Ajastus: `30 3 * * *` (06:30 EET). Virheenkäsittely: ks. [Virheenkäsittely ja retry-logiikka](#virheenkäsittely-ja-retry-logiikka). Base URL: `https://hri.fi/data/api/3/action/`.

Datasetit tallennetaan AS2 `Document`-objekteina, kategoriat `OrderedCollection`-objekteina. Kaksikieliset metatiedot: `nameMap.fi` / `nameMap.sv`, `summaryMap.fi` / `summaryMap.sv`.

**Kenttäkartoitus:**

| AS2-kenttä | CKAN-kenttä |
|---|---|
| `published` | `metadata_created` |
| `updated` | `metadata_modified` |
| `nameMap.fi` | `result.title` |
| `nameMap.sv` | `result.title_translated.sv` |

`actor` asetetaan aina organisaatiotasolle (HRI), ei yksittäiselle CKAN-käyttäjälle.

---

## Cloud Run Job: Voikko-tagienrikastus (#6)

Ajastus: `30 * * * *` (30 min välein). Virheenkäsittely: ks. [Virheenkäsittely ja retry-logiikka](#virheenkäsittely-ja-retry-logiikka). Käsittelee erissä (100 kpl) objektit joilla `tags_enriched = FALSE`.

**Analyysi:** `libvoikko` palauttaa sanan `BASEFORM`-lemman. Käytetyt sanaluokat: `nimisana`, `erisnimi`, `paikannimi`, `paikannimi_ulkomaat`, `teonsana`, `määrite`. Hyjätään: `suhdesana`, `sidesana`, `kieltosana`, `asemosana`, `partikkeli`.

Top-16 yleisintä lemmaa tallennetaan `tags`-sarakkeeseen `MERGE UPDATE` -operaatiolla. Voikko-job on `tags`-sarakkeen ainoa kirjoittaja. Job asettaa aina `tags_enriched = TRUE` käsittelyn jälkeen — myös silloin kun tulos on tyhjä lista. Tämä estää rivin jäämisen ikuiseen uudelleenkäsittelysilmukkaan.

```sql
MERGE activitystreams.objects T
USING enriched_batch S ON T.id = S.id
WHEN MATCHED THEN
    UPDATE SET
        T.tags          = S.tags,
        T.tags_enriched = TRUE
```

---

## Cloud Run endpoint: OG-scraper (#8 / #23)

`POST /ap/scrape { "url": "https://..." }` — palauttaa AS2 `Article`-objektin ja tallentaa sen `activitystreams.objects`-tauluun.

**Autentikaatio:** Cloud IAM -palvelutili (`--no-allow-unauthenticated`). OG-scraper on puhdas backend-prosessi ilman käyttäjäkontekstia — se kirjoittaa `activitystreams`-avoimelle puolelle palvelutilin IAM-oikeuksilla. Käyttäjän `id_token`ia ei käytetä eikä vaadita.

> [!NOTE]
> OG-scraper resolvoituu käyttäjän artikkelin luomisprosessissa: käyttäjä lisää linkin → UI kutsuu `/ap/scrape` → AS2-objekti luodaan avoimelle puolelle. Tämän jälkeen käyttäjä luo artikkelin `POST /ap/activities` -kutsulla **omalla `id_token`illaan** → sosiaalinen data kirjataan `activitystreams_social`-puolelle.

**Verkkoturva ja SSRF-suojaus — ei domain-whitelistia:** OG-scraper ei käytä domain-whitelistia. Suojaus toteutetaan URL- ja IP-validoinnilla sekä rajoituksilla HTTP-pyyntöihin:

- URL resolvoidaan IP-osoitteeksi ennen pyyntöä. Pyyntö hylätään `403 Forbidden`, jos osoite resolvoituu:
  - localhostiin (`127.0.0.1`, `::1`, `localhost`)
  - RFC1918-private-osoitteisiin (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
  - link-local-osoitteisiin (`169.254.0.0/16`) tai metadata-IP-osoitteisiin (esim. `169.254.169.254`)
- HTTP-redirect-ketjut tarkistetaan samalla tavalla: jos jokin askel päätyy yksityiseen osoiteavaruuteen, pyyntö katkaistaan ja palautetaan `403 Forbidden`.
- Scraper käyttää pientä timeout-arvoa (10 s) ja rajoittaa vastekoon (2 MB); vastauksen streamaus katkaistaan `</head>`-tagin jälkeen.
- Endpoint rajoittaa pyyntöjä per IP: 10 pyyntöä / 60 s. Ylitys → `429 Too Many Requests`.
- Käyttäjä voi syöttää vain yhden URL:n kerrallaan; artikkelissa säilytetään vain ensimmäinen siihen liitetty URL.

**Duplikaattipyynnöt:** Sama URL kahdesti tuottaa saman `id`:n (`sha256(url)`), joten MERGE-operaatio hoitaa duplikaatin hiljaisesti.

**Kenttäkartoitus** (ks. myös [`id`-kentän kaava](#id-kentän-kaava-lähteittäin)):

| AS2-kenttä | OG-lähde | Fallback |
|---|---|---|
| `name` | `og:title` | `<title>` |
| `summary` | `og:description` | `<meta name=description>` |
| `image` | `og:image` | — |
| `published` | `article:published_time` | Scrape-hetki |
| `updated` | `article:modified_time` | Scrape-hetki |

`published` käyttää scrape-hetkeä fallbackina (toisin kuin RSS-job, joka ohittaa artikkelin). Tämä on hyväksyttää koska OG-scraper on käyttäjän manuaalisesti käynnistämä toiminto.

---

## Cloud Run Job: OG-rikastusjob (#24)

Ajastus: `5 * * * *` (5 min RSS-jobin jälkeen). Virheenkäsittely: ks. [Virheenkäsittely ja retry-logiikka](#virheenkäsittely-ja-retry-logiikka).

Käsittelee erissä (100 kpl) RSS-peräiset rivit joilla `og_enriched = FALSE`. Käyttää samaa HTTP/OG-moduulia kuin OG-scraper (#23) — robots.txt-cache (24 h), IP-osoitevalidointi, redirect-ketjutarkistus, stream `</head>`-tagiin.

**Ei domain-whitelistia.** SSRF-suojaus toteutetaan samalla IP-osoitevalidoinnilla kuin OG-scraperissa (#23).

Onnistunut URL → `og_enriched = TRUE`, `og_enriched_error = NULL`.
Epäonnistunut URL → `og_enriched = TRUE`, `og_enriched_error = <virheviesti>`.

Epäonnistuneet rivit käsitellään erikseen: `WHERE og_enriched = TRUE AND og_enriched_error IS NOT NULL`.

**Kenttäsäännöt:**

| AS2-kenttä | Sääntö |
|---|---|
| `name` | `longer(rss, og)` — trim ennen vertailua |
| `summary` | `longer(rss, og)` — trim ennen vertailua |
| `image` | OG voittaa aina jos saatavilla |
| `published` | RSS-arvo säilyy; OG täydentää vain jos RSS-arvo puuttuu |
| `updated` | OG voittaa jos saatavilla |

---

## Cloud Run: kirjoituspalvelu (#7)

Vastaanottaa AS2-aktiviteetteja JSON:na käyttäjän `id_token`-tokenilla. Validoi tyypin ja pakolliset kentät. Kirjoittaa `activitystreams_social.activities`- ja tarvittaessa `activitystreams_social.likes`-tauluun BigQuery Storage Write API:lla.

**Autentikaatio:** Käyttäjän Google `id_token` pakollinen. Ks. [Autentikaatio ja valtuutus](#autentikaatio-ja-valtuutus).

### Tuetut aktiviteetit

| Aktiviteetti | Kirjoitetaan |
|---|---|
| `Create` | `activitystreams_social.activities` (kommentti, vastaus, käyttäjän artikkeli) |
| `Update` | `activitystreams_social.activities` (päivitetty objekti) |
| `Delete` | `activitystreams_social.activities` + `activitystreams.objects.deleted = TRUE` |
| `Add` | `activitystreams_social.activities` (tagi-operaatio) |
| `Remove` | `activitystreams_social.activities` (tagi-operaatio) |
| `Like` | `activitystreams_social.activities` + `activitystreams_social.likes` |
| `Dislike` | ❌ 400 Bad Request |
| `Announce` | ❌ 400 Bad Request |
| `Undo` | ❌ 400 Bad Request — ks. alla |

**Miksi `Undo` ei ole tuettu?** Tykkäys tallennetaan anonymisoituna: `likes`-taulussa ei ole käyttäjätunnistetta jolla tykkäyksen voisi yksilöidä jälkikäteen. Kun data on anonymisoitu, käyttäjä ei enää omista sitä eikä voi siksi perua toimintoa. Tämä on tietoinen arkkitehtuuripäätös, ei puuttuva toteutus.

### Kommenttiketjun syvyysvalidointi

```
in_reply_to kohde on Article → luo kommentti (taso 1)
in_reply_to kohde on Comment → luo vastaus (taso 2)
in_reply_to kohde on Note/vastaus → 400 Bad Request
```

`thread_root` täydennetään automaattisesti palvelimella.

### Delete-semantiikka

Delete ei poista tietokannasta. `activitystreams_social.activities`-tauluun kirjataan `Delete`-tapahtuma ja `activitystreams.objects.deleted` asetetaan `TRUE`:ksi. Poistettu kommentti näytetään paikkamerkkinä `[kommentti poistettu]` jos sillä on vastauksia.

### Update-semantiikka

Käyttäjä voi päivittää **vain oman kommenttinsa** sisällön. Organisaatioiden julkaisemia objekteja (RSS, Ahjo, HRI, OG-scraped) ei voi päivittää `Update`-aktiviteetilla — ne ovat jobien omistamia.

- `actor` validoidaan: `Update`-pyynnön `actor`-IRI:n `sub`-osa täytyy vastata alkuperäisen `Create`-aktiviteetin `actor`-IRI:n `sub`-osaa. Jos ne eivät täsmää, palautetaan `403 Forbidden`.
- `published` ei muutu — vain `object_json` ja `updated` päivittyvät.
- `Update`-aktiviteetti kirjoitetaan `activitystreams_social.activities`-tauluun ja `activitystreams.objects.object_json` päivitetään MERGE-operaatiolla.
- `thread_root`-artikkelin `updated` päivittyy muokatusta kommentista (ks. [Tykkäyslaskuri ja updated-aikaleima](#tykkäyslaskuri-ja-updated-aikaleima-1112)).

---

## Cloud Run: outbox-endpoint (#10)

`GET /ap/outbox?tag=asuminen&n=50`

**Autentikaatio:** Julkinen — ei vaadi tokenia. Lukee `activitystreams`-datasettia.

### Paginaatiomalli

Client pyytää aina alusta `n` kappaletta. Ei kursoreja, ei sivunumeroita. Yli 500:n `n`-arvo palauttaa `400 Bad Request`.

```
GET /ap/outbox?tag=asuminen&n=5     → top-5
GET /ap/outbox?tag=asuminen&n=50    → top-50 (sisältää edellisen 5, client suodattaa)
GET /ap/outbox?tag=asuminen&n=500   → top-500 (maksimi)
```

Kun `totalItems > 500`, UI näyttää tagipilven josta voi tehdä uuden haun ([uutisseuranta.github.io #16](https://github.com/uutisseuranta/uutisseuranta.github.io/issues/16)).
