# Technical Design: gcs-activitystreams

Tämä dokumentti kokoaa kaikkien tikettien (#1–#12) arkkitehtuuripäätökset yhdeksi luettavaksi kokonaisuudeksi. Arkkitehtuuriperiaatteet ovat [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) -tiedostossa.

---

## Yleiskuva

```
https://activitystreams.uutisseuranta.net/
  └── GET /ap/outbox?tag=asuminen&n=50  ← Cloud Run (query-api) → BigQuery
  └── POST /ap/scrape { url }           ← Cloud Run (og-scraper) → BigQuery
  └── POST /ap/activities               ← Cloud Run (write-api) → BigQuery

Kirjoittajat per taulu:

  activitystreams.objects    ← jobit #2 (RSS), #3 (Ahjo), #4 (HRI), #8 (OG-scraper)
                                suora MERGE-kirjoitus, tavallinen taulu
                                tags-sarakkeen omistaja: Voikko-job (#6)
                                like_count-sarakkeen omistaja: likes-sync-job (#11)

  activitystreams.activities ← kirjoituspalvelu (#7): käyttäjätoiminnot
                                (kommentit, tykkäykset, käyttäjän luomat objektit)
                                append-only event log

  activitystreams.likes      ← kirjoituspalvelu (#7): Like-tapahtumat
  activitystreams.config     ← jobit päivittävät last_fetched_at ja dynaamiset URL:t
```

### GCP-konfiguraatio

| Muuttuja | Arvo |
|---|---|
| **Domain** | `activitystreams.uutisseuranta.net` |
| **GCP-projekti** | `activitystreams` |
| **BigQuery dataset** | `activitystreams` |
| **Sijainti** | `europe-north1` |
| **GitHub-repo** | `uutisseuranta/gcs-activitystreams` |

### Cloud Scheduler -ajastukset

| Job | Cron | Kellonaika (EET) |
|---|---|---|
| `rss-fetch-job` | `0 * * * *` | kerran tunnissa |
| `ahjo-fetch-job` | `0 3 * * *` | 06:00 |
| `hri-fetch-job` | `30 3 * * *` | 06:30 |
| `voikko-enrich-job` | `30 * * * *` | 30 min välein |
| `likes-sync-job` | `*/15 * * * *` | 15 min välein |
| `activity-updated-job` | `*/15 * * * *` | 15 min välein |

---

## BigQuery-skeema (#1)

### `activitystreams.objects` — artikkelit, päätökset, datasetit

```sql
CREATE TABLE activitystreams.objects (
  id           STRING    NOT NULL OPTIONS(description='AS2 id – domain-pohjainen IRI, primääriavain'),
  source       STRING    NOT NULL OPTIONS(description='Lähde: rss | ahjo | hri | scraped | user'),
  published    TIMESTAMP          OPTIONS(description='AS2 published – NULL jos metatietoa ei saatavilla'),
  updated      TIMESTAMP          OPTIONS(description='AS2 updated – päivittyy käyttäjäaktiivisuudesta (#12)'),
  tags         STRING    REPEATED OPTIONS(description='Lemmatisoidut tagit (Voikko #6)'),
  like_count   INT64     NOT NULL DEFAULT 0
                         OPTIONS(description='Tykkäysmäärä activitystreams.likes-taulusta, päivitetään likes-sync-jobilla (#11)'),
  deleted      BOOL      NOT NULL DEFAULT FALSE,
  object_json  JSON               OPTIONS(description='Koko AS2-objekti natiivina JSON-tyypinä')
)
PARTITION BY DATE(COALESCE(published, updated))
CLUSTER BY source, published;
```

> **Miksi `published` on nullable?**
> Fallback-tapauksessa (OG-scrape ilman `article:published_time`) julkaisuhetkeä ei tiedetä. `published = NULL`, `updated = scrape-hetki`. Järjestyksessä `published DESC NULLS LAST`.

### `activitystreams.activities` — append-only event log

```sql
CREATE TABLE activitystreams.activities (
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

### `activitystreams.likes` — tykkäykset

```sql
CREATE TABLE activitystreams.likes (
  activity_id   STRING    NOT NULL,
  actor         STRING    NOT NULL,
  object_id     STRING    NOT NULL,
  published     TIMESTAMP NOT NULL
)
PARTITION BY DATE(published)
CLUSTER BY object_id, actor;
```

### `activitystreams.config` — dynaaminen konfiguraatio

```sql
CREATE TABLE activitystreams.config (
  key           STRING    NOT NULL,
  value         STRING    NOT NULL,
  updated_at    TIMESTAMP NOT NULL,
  updated_by    STRING
);
```

| key | Kuvaus |
|---|---|
| `valtioneuvosto.rss_url` | RSS-URL löydetty autodiscoveryllä |
| `rss.last_fetched_at` | RSS-jobin viimeisin onnistunut ajo |
| `ahjo.last_fetched_at` | Ahjo-jobin viimeisin onnistunut ajo |
| `hri.last_fetched_at` | HRI-jobin viimeisin onnistunut ajo |

### `id`-kentän kaava lähteittäin

| Lähde | `id`-kaava |
|---|---|
| RSS | `https://activitystreams.uutisseuranta.net/ap/objects/articles/{source}/{sha256(url)}` |
| Ahjo | `https://activitystreams.uutisseuranta.net/ap/objects/decisions/helsinki/{register_id}` |
| HRI-datasetti | `https://activitystreams.uutisseuranta.net/ap/objects/hri/datasets/{ckan-uuid}` |
| HRI-kategoria | `https://activitystreams.uutisseuranta.net/ap/objects/hri/groups/{group-name}` |
| OG-scrapattu | `https://activitystreams.uutisseuranta.net/ap/objects/scraped/{sha256(url)}` |

### `source`-sarake vs. `source`-query-parametri

`source`-sarake **on** `objects`-taulun sisäinen kenttä — se kertoo mistä objekti on peräisin (rss, ahjo, hri, scraped, user). Se ei koskaan näy API:n query-parametreina. `GET /ap/outbox?source=ahjo` palauttaa `400 Bad Request`. Client suodattaa aina tageilla, ei lähteellä.

---

## `published` ja `updated` lähteittäin (#9)

| Lähde | `published` | `updated` |
|---|---|---|
| RSS-artikkeli | `<pubDate>` tai `<dc:date>` | `<atom:updated>` jos saatavilla |
| OpenAhjo-päätös | `latest_decision_date` | API:n `modified` jos muuttunut |
| HRI-datasetti | `metadata_created` | `metadata_modified` |
| OG-scrapattu | `article:published_time` OG-tagista | `article:modified_time` |
| Fallback (ei metatietoa) | `NULL` — merkitään lokiin | Scrape-hetki |

`published` ei koskaan muutu objektin päivityksissä. `updated` päivittyy myös kun artikkeliin kohdistuu käyttäjäaktiivisuutta (kommentti, tykkäys) — ks. #12.

---

## Cloud Run Job: RSS-syötteet (#2)

Ajastus: `0 * * * *` (kerran tunnissa).

**Lähteet:**

| Lähde | RSS-URL |
|---|---|
| Helsingin Sanomat | `https://www.hs.fi/rss/tuoreimmat.xml` |
| Iltalehti | `https://www.iltalehti.fi/rss/uutiset.xml` |
| Ilta-Sanomat | `https://www.is.fi/rss/tuoreimmat.xml` |
| Kauppalehti | `https://feeds.kauppalehti.fi/rss/main` |
| MTV Uutiset | `https://www.mtvuutiset.fi/rss.xml` |
| Valtioneuvosto | autodiscovery → tallennetaan `config`-tauluun |

**Tallennuslogiikka:** MERGE-operaatio `id`-kentän perusteella. `tags`- ja `like_count`-sarakkeet jätetään `WHEN MATCHED` -haaran ulkopuolelle — Voikko-job (#6) ja likes-sync-job (#11) omistavat ne. `WHEN NOT MATCHED`: `tags = []`, `like_count = 0`.

```sql
MERGE activitystreams.objects T
USING activitystreams.objects_temp S ON T.id = S.id
WHEN MATCHED AND S.updated > T.updated THEN
    UPDATE SET
        T.object_json = S.object_json,
        T.published   = S.published,
        T.updated     = S.updated,
        T.source      = S.source
        -- tags, like_count ja deleted jätetään tarkoituksella pois
WHEN NOT MATCHED THEN
    INSERT (id, source, published, updated, tags, like_count, deleted, object_json)
    VALUES (S.id, S.source, S.published, S.updated, [], 0, FALSE, S.object_json)
```

**Kuva-prioriteetti:** (1) `<media:thumbnail>`, (2) `<enclosure type="image/*">`, (3) `<image>` kanavan tasolla.

---

## Cloud Run Job: OpenAhjo-päätökset (#3)

Ajastus: `0 3 * * *` (06:00 EET). Base URL: `http://dev.hel.fi/openahjo/v1`.

**Fetch-ikkuna:** Ei kiinteää `-24h`. Käytetään `config`-taulun `ahjo.last_fetched_at`-arvoa — päivitetään vasta onnistuneen ajon jälkeen.

**Kenttäkartoitus:**

| AS2-kenttä | OpenAhjo-kenttä |
|---|---|
| `id` | `register_id` (esim. `HEL-2026-012345`) |
| `name` | `subject` |
| `summary` | `agenda_item.content` (resolution) |
| `published` | `latest_decision_date` |
| `attributedTo.name` | `policymaker.name` |

---

## Cloud Run Job: HRI-datasetit (#4)

Ajastus: `30 3 * * *` (06:30 EET). Base URL: `https://hri.fi/data/api/3/action/`.

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

Ajastus: `30 * * * *` (30 min välein). Käsittelee erissä (100 kpl) objektit joilla `tags_enriched = FALSE`.

**Analyysi:** `libvoikko` palauttaa sanan `BASEFORM`-lemman. Käytetyt sanaluokat: `nimisana`, `erisnimi`, `paikannimi`, `paikannimi_ulkomaat`, `teonsana`, `määrite`. Hylätään: `suhdesana`, `sidesana`, `kieltosana`, `asemosana`, `partikkeli`.

Top-16 yleisintä lemmaa tallennetaan `tags`-sarakkeeseen `MERGE UPDATE` -operaatiolla. Voikko-job merkitsee rivin `tags_enriched = TRUE` aina käsittelyn jälkeen — myös silloin kun tulos on tyhjä lista, jotta rivi ei jää ikuiseen uudelleenkäsittelysilmukkaan.

---

## Cloud Run endpoint: OG-scraper (#8)

`POST /ap/scrape { "url": "https://..." }` — palauttaa AS2 `Article`-objektin ja tallentaa sen `objects`-tauluun.

**Kenttäkartoitus:**

| AS2-kenttä | OG-lähde | Fallback |
|---|---|---|
| `name` | `og:title` | `<title>` |
| `summary` | `og:description` | `<meta name=description>` |
| `image` | `og:image` | — |
| `published` | `article:published_time` | `NULL` (merkitään lokiin) |
| `updated` | `article:modified_time` | Scrape-hetki |

---

## Cloud Run: kirjoituspalvelu (#7)

Vastaanottaa AS2-aktiviteetteja JSON:na. Validoi tyypin ja pakolliset kentät. Kirjoittaa `activities`- ja tarvittaessa `likes`-tauluun BigQuery Storage Write API:lla.

### Tuetut aktiviteetit

| Aktiviteetti | Kirjoitetaan |
|---|---|
| `Create` | `activities` (kommentti, vastaus, käyttäjän artikkeli) |
| `Update` | `activities` (päivitetty objekti) |
| `Delete` | `activities` (`deleted=TRUE` materialized viewissä) |
| `Add` | `activities` (tagi-operaatio) |
| `Remove` | `activities` (tagi-operaatio) |
| `Like` | `activities` + `likes` |
| `Dislike` | ❌ 400 Bad Request |
| `Announce` | ❌ 400 Bad Request |
| `Undo` | ❌ 400 Bad Request |

### Kommenttiketjun syvyysvalidointi

```
in_reply_to kohde on Article → luo kommentti (taso 1)
in_reply_to kohde on Comment → luo vastaus (taso 2)
in_reply_to kohde on Note/vastaus → 400 Bad Request
```

`thread_root` täydennetään automaattisesti palvelimella.

### Delete-semantiikka

Delete ei poista tietokannasta. Poistettu kommentti näytetään paikkamerkkinä `[kommentti poistettu]` jos sillä on vastauksia.

---

## Cloud Run: outbox-endpoint (#10)

`GET /ap/outbox?tag=asuminen&n=50`

### Paginaatiomalli

Client pyytää aina alusta `n` kappaletta. Ei kursoreja, ei sivunumeroita. Yli 500:n `n`-arvo palauttaa `400 Bad Request`.

```
GET /ap/outbox?tag=asuminen&n=5     → top-5
GET /ap/outbox?tag=asuminen&n=50    → top-50 (sisältää edellisen 5, client suodattaa)
GET /ap/outbox?tag=asuminen&n=500   → top-500 (maksimi)
```

Kun `totalItems > 500`, UI näyttää tagipilven josta voi tehdä uuden haun ([uutisseuranta.github.io #16](https://github.com/uutisseuranta/uutisseuranta.github.io/issues/16)).

### AS2-rakenne

```json
{
  "@context": "https://www.w3.org/ns/activitystreams",
  "type": "OrderedCollection",
  "id": "https://activitystreams.uutisseuranta.net/ap/outbox?tag=asuminen&n=50",
  "totalItems": 4821,
  "orderedItems": [ ]
}
```

Ei `next`, ei `prev`, ei `first`. `totalItems` kertoo clientille paljonko pyytää maksimissaan.

### Järjestys

```sql
ORDER BY
  relevance     DESC,            -- osuvien hakutagien määrä
  like_count    DESC,            -- suorat tykkäykset objects-taulusta
  updated       DESC,            -- viimeisin aktiivisuus
  published     DESC NULLS LAST, -- alkuperäinen julkaisu (fallback-objektit loppuun)
  id            ASC
```

### Query-parametrit

| Parametri | Kuvaus | Oletus | Maksimi |
|---|---|---|---|
| `tag` | Toistuva. Pakollinen — `400` jos puuttuu. | — | — |
| `n` | Palautettavien määrä. Yli 500 → `400`. | 50 | 500 |
| `after` | ISO 8601. Aktivoi partition-karsintaa. | — | — |

### `totalItems`-caching

`COUNT(*)`-kysely cachetetaan Cloud Run -muistissa 5 minuutiksi tag-kombinaatiota kohden. Cache on instanssikohtainen — `totalItems` on approksimatiivinen arvo.

---

## Tykkäyslaskuri: `like_count`-sarake (#11)

`like_count INT64 NOT NULL DEFAULT 0` on suoraan `activitystreams.objects`-taulussa. Laskentajob (15 min välein) laskee tykkäysmäärät `activitystreams.likes`-taulusta ja päivittää sarakkeen MERGE-operaatiolla.

```sql
MERGE activitystreams.objects T
USING (
  SELECT object_id, COUNT(*) AS cnt
  FROM activitystreams.likes
  GROUP BY object_id
) S ON T.id = S.object_id
WHEN MATCHED AND T.deleted = FALSE
  THEN UPDATE SET T.like_count = S.cnt
```

- Laskuri voi vain **kasvaa** — `Undo Like` ei ole mahdollinen
- Laskuri on anonyymi — ei tietoa kuka tykkäsi
- `deleted = FALSE` -ehto estää poistettujen objektien päivittymisen

### `likes`-kenttä AS2-vastauksessa

Query-API upottaa `like_count`-sarakkeen arvon palautushetkellä suoraan AS2-objektiin:

```json
{
  "type": "Article",
  "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/hs/abc123",
  "name": "Helsingin kaupunginvaltuusto hyväksyi asunto-ohjelman",
  "likes": 42
}
```

`object_json`-sarakkeeseen ei tallenneta `likes`-kenttää pysyvästi — se lasketaan lennossa.

---

## `updated`-aikaleima ja relevanssijärjestys (#12)

Kun artikkeliin kohdistuu käyttäjäaktiivisuutta, **thread_root-artikkelin** `updated` päivittyy:

| Tapahtuma | Päivittää `updated` |
|---|---|
| Artikkelin sisältö muuttuu | ✅ |
| Käyttäjä kommentoi artikkelia | ✅ |
| Käyttäjä tykkää artikkelista | ✅ |
| Käyttäjä kommentoi kommenttia | ✅ (thread_root) |
| Käyttäjä tykkää kommentista | ✅ (thread_root) |

```sql
-- Laskentajob
SELECT COALESCE(thread_root, object_id) AS root_url,
       MAX(published) AS last_activity_at
FROM activitystreams.activities
WHERE type IN ('Like', 'Create') AND deleted = FALSE
GROUP BY root_url
```

`COALESCE(thread_root, object_id)` varmistaa että artikkeliin suoraan kohdistuvat tykkäykset (joilla `thread_root = NULL`) käsitellään oikein.

`updated` ei koskaan kulje taaksepäin (`AND @last_activity_at > updated`).

Avoimen datan BigQueryyn kirjoitetaan vain aikaleima — ei tietoa kuka kommentoi tai tykkäsi.

---

## Kustannusarvio

| Resurssi | Arvio | Hinta |
|---|---|---|
| BigQuery kyselyt (100k riviä, n=500) | ~110 MB/pyyntö | ~$0.0007/pyyntö |
| Ilmainen 1 TB/kk -kiintiö | ~9 000 pyyntöä 100k rivillä | $0/kk |
| Cloud Run Job -suoritukset | <30s/ajo | $0/kk (ilmainen taso) |
| Cloud Run palvelu | ~1000 pyyntöä/kk | $0/kk (ilmainen taso) |

`after`-parametri aktivoi BigQueryn partitiokarsintaa ja voi leikata skannatun datan murto-osaan.

---

## Liittyy

- [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) — arkkitehtuuriperiaatteet
- #1 AS2-arkkitehtuuri + BigQuery-skeema
- #2 RSS-jobi
- #3 Ahjo-jobi
- #4 HRI-jobi
- #5 Firestore-vaihtoehto (BigQuery valittu)
- #6 Voikko-tagienrikastus
- #7 Kirjoituspalvelu
- #8 OG-scraper
- #9 Design guidelines
- #10 Outbox-endpoint
- #11 Tykkäyslaskuri
- #12 `updated`-aikaleima
