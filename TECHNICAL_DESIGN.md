# Technical Design: gcs-activitystreams

Tämä dokumentti kokoaa kaikkien tikettien (#1–#13) arkkitehtuuripäätökset yhdeksi luettavaksi kokonaisuudeksi. Arkkitehtuuriperiaatteet ovat [DESIGN_GUIDELINES.md](./DESIGN_GUIDELINES.md) -tiedostossa.

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
  updated      TIMESTAMP          OPTIONS(description='AS2 updated – scrape-hetki fallbackissa'),
  tags         STRING    REPEATED OPTIONS(description='Lemmatisoidut tagit (Voikko #6) + likes:N-tagi (#11)'),
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

**Tallennuslogiikka:** MERGE-operaatio `id`-kentän perusteella. `tags`-sarake jätetään `WHEN MATCHED` -haaran ulkopuolelle — Voikko-job (#6) omistaa sen. `WHEN NOT MATCHED`: `tags = []`.

```sql
MERGE activitystreams.objects T
USING activitystreams.objects_temp S ON T.id = S.id
WHEN MATCHED AND S.updated > T.updated THEN
    UPDATE SET
        T.object_json = S.object_json,
        T.published   = S.published,
        T.updated     = S.updated,
        T.source      = S.source
        -- tags ja deleted jätetään tarkoituksella pois
WHEN NOT MATCHED THEN
    INSERT (id, source, published, updated, tags, deleted, object_json)
    VALUES (S.id, S.source, S.published, S.updated, [], FALSE, S.object_json)
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

Ajastus: `30 * * * *` (30 min välein). Käsittelee erissä (100 kpl) objektit joilla `ARRAY_LENGTH(IFNULL(tags, [])) = 0`.

**Analyysi:** `libvoikko` palauttaa sanan `BASEFORM`-lemman. Käytetyt sanaluokat: `nimisana`, `erisnimi`, `paikannimi`, `paikannimi_ulkomaat`, `teonsana`, `määrite`. Hylätään: `suhdesana`, `sidesana`, `kieltosana`, `asemosana`, `partikkeli`.

Top-16 yleisintä lemmaa tallennetaan `tags`-sarakkeeseen `MERGE UPDATE` -operaatiolla. Voikko-job on `tags`-sarakkeen ainoa kirjoittaja lähdesisällölle.

---

## Cloud Run endpoint: OG-scraper (#8)

`POST /ap/scrape { "url": "https://..." }` — palauttaa AS2 `Article`-objektin ja tallentaa sen `objects`-tauluun.

### Kenttäkartoitus

| AS2-kenttä | OG-lähde | Fallback |
|---|---|---|
| `name` | `og:title` | `<title>` |
| `summary` | `og:description` | `<meta name=description>` |
| `image` | `og:image` | — |
| `published` | `article:published_time` | `NULL` (merkitään lokiin) |
| `updated` | `article:modified_time` | Scrape-hetki |

### HTTP-pyyntö ja User-Agent

```python
HEADERS = {
    "User-Agent": "uutisseuranta-og-scraper/1.0 (+https://activitystreams.uutisseuranta.net/)",
    "Accept": "text/html,application/xhtml+xml",
}
TIMEOUT = 10  # sekuntia
MAX_REDIRECTS = 5
MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2 MB — lue vain header + alku
```

- **User-Agent** sisältää projektin nimen ja URL:n, jotta kohdesivu voi tunnistaa scraperin ja tarvittaessa estää tai ottaa yhteyttä.
- **Timeout 10 s** — ei odoteta hitaita sivustoja. `408 Request Timeout` clientille.
- **Max redirects 5** — suojaa redirect-silmukoilta.
- **2 MB content limit** — luetaan vain sivun alku; metatiedot ovat aina `<head>`-tagissa.

### Rate limiting

| Sääntö | Arvo | Perustelu |
|---|---|---|
| Max pyyntöjä per IP / 60 s | 10 | Estää yksittäisen käyttäjän massascrapen |
| Max pyyntöjä samaan domainiin / tunti | 60 | Ei rasiteta samaa palvelinta kohtuuttomasti |
| Viive peräkkäisten pyyntöjen välillä (samaan domainiin) | 2 s | Ystävällinen crawl-delay |
| Robots.txt | Tarkistetaan — `Disallow: /` estää scrapen | `Crawl-delay`-direktiivi noudatetaan |

Rajoitukset toteutetaan Cloud Run -palvelun muistissa per instanssi. Jos rate limit ylittyy: `429 Too Many Requests` + `Retry-After`-otsake.

### Duplikaattikäsittely

`id = sha256(url)` — sama URL tuottaa aina saman `id`:n. MERGE-operaatio päivittää olemassa olevan objektin eikä luo duplikaattia.

---

## Cloud Run: kirjoituspalvelu (#7)

Vastaanottaa AS2-aktiviteetteja JSON:na. Validoi tyypin ja pakolliset kentät. Kirjoittaa `activities`- ja tarvittaessa `likes`-tauluun BigQuery Storage Write API:lla.

### Tuetut aktiviteetit

| Aktiviteetti | Kirjoitetaan |
|---|---|
| `Create` | `activities` (kommentti, vastaus, käyttäjän artikkeli) |
| `Update` | `activities` (päivitetty objekti) |
| `Delete` | `activities` + `objects.deleted = TRUE` |
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

### Delete-semantiikka (#13)

```
POST /ap/activities
{
  "type": "Delete",
  "actor": "https://...",
  "object": "https://activitystreams.uutisseuranta.net/ap/objects/..."
}
```

1. Palvelin tarkistaa että `actor` on objektin alkuperäinen luoja.
2. Kirjoitetaan `Delete`-aktiviteetti `activities`-tauluun (append-only).
3. `objects`-taulun `deleted`-sarake päivitetään `TRUE`:ksi.
4. Poistettu objekti ei enää palaudu `GET /ap/outbox` -kyselyissä (`WHERE deleted = FALSE`).

**Näkyvyys ketjussa:**

| Tilanne | Näytetään |
|---|---|
| Poistettu kommentti, ei vastauksia | Piilotetaan kokonaan |
| Poistettu kommentti, on vastauksia | `[kommentti poistettu]` -paikkamerkki |
| Poistettu artikkeli | Piilotetaan outbox-hauista |

Datan säilyminen `activities`-taulussa mahdollistaa moderoinnin jälkikäteen.

---

## Cloud Run: outbox-endpoint (#10)

`GET /ap/outbox?tag=asuminen&n=50`

### Paginaatiomalli

Client pyytää aina alusta `n` kappaletta. Ei kursoreja, ei sivunumeroita. Maksimi `n=500` — hinta- ja suorituskykyrajoite (ks. DESIGN_GUIDELINES).

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
  tag_hits      DESC,            -- tagiosumien määrä
  like_count    DESC,            -- likes:N-tagista luettu
  updated       DESC,            -- viimeisin aktiivisuus
  published     DESC NULLS LAST, -- alkuperäinen julkaisu (fallback-objektit loppuun)
  id            ASC
```

### Query-parametrit

| Parametri | Kuvaus | Oletus | Maksimi |
|---|---|---|---|
| `tag` | Toistuva. Pakollinen — `400` jos puuttuu. | — | — |
| `n` | Palautettavien määrä. | 50 | 500 |
| `after` | ISO 8601. Aktivoi partition-karsintaa. | — | — |

### `totalItems`-caching

`COUNT(*)`-kysely cachetetaan Cloud Run -muistissa 5 minuutiksi tag-kombinaatiota kohden.

---

## Tykkäyslaskuri: `likes:N`-tagi (#11)

Laskentajob (15 min välein) laskee tykkäysmäärät `likes`-taulusta ja päivittää `objects`-taulun `tags`-sarakkeeseen tagin muotoa `likes:N`.

```sql
UPDATE open_dataset.objects
SET tags = ARRAY(
  SELECT t FROM UNNEST(tags) t WHERE NOT STARTS_WITH(t, 'likes:')
  UNION ALL
  SELECT CONCAT('likes:', CAST(@like_count AS STRING))
)
WHERE id = @object_url
```

- Laskuri voi vain **kasvaa** — `Undo Like` ei ole mahdollinen
- Tagi on anonyymi — ei tietoa kuka tykkäsi
- Yksi `likes:`-tagi per objekti — päivitys korvaa edellisen
- `likes:N`-tagit **eivät** näy hakufilttereissä eivätkä UI:n tagipilvessä

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
SELECT thread_root_url, MAX(created_at) AS last_activity_at
FROM social_dataset.activities
WHERE type IN ('Like', 'Create') AND deleted = FALSE
GROUP BY thread_root_url
```

`updated` ei koskaan kulje taaksepäin (`AND @last_activity_at > updated`).

Avoimen datan BigQueryyn kirjoitetaan vain aikaleima — ei tietoa kuka kommentoi tai tykkäsi.

---

## Virheenkäsittely ja retry-logiikka

Kaikissa Cloud Run Job -ajoissa (RSS #2, Ahjo #3, HRI #4, Voikko #6) noudatetaan yhtenäistä virheenkäsittelymallia.

### `last_fetched_at` — päivitetään vain onnistuneesta ajosta

`config`-taulun `last_fetched_at` -arvo päivitetään **ainoastaan kun koko ajo on suoritettu onnistuneesti** (HTTP 2xx + BigQuery MERGE ok). Epäonnistunut ajo ei päivitä arvoa — seuraava ajo käyttää edellistä onnistunutta ajankohtaa fetch-ikkunana, jolloin mikään artikkeli ei jää väliin.

```
Ajo 1: onnistuu  → last_fetched_at = T1
Ajo 2: epäonnistuu (lähde 500) → last_fetched_at = T1 (ei muutu)
Ajo 3: onnistuu  → hakee T1:stä eteenpäin → last_fetched_at = T3
```

### Retry-strategia HTTP-virheille

| HTTP-statuskoodi | Toiminto |
|---|---|
| `2xx` | Jatketaan normaalisti |
| `429 Too Many Requests` | Odotetaan `Retry-After`-otsakkeen mukainen aika (tai 60 s), yritetään uudelleen max 3 kertaa |
| `5xx` (palvelinvirhe) | Eksponentiaalinen backoff: 30 s, 5 min, 15 min. Max 3 yritystä. |
| `404 Not Found` | Kirjataan lokiin, jatketaan muiden lähteiden käsittelyä. Ei retrytä. |
| `Connection error / timeout` | Sama kuin `5xx`. |

Cloud Run Job ei kaada koko ajoa yksittäisen RSS-lähteen virheestä — virhe kirjataan lokiin, ajo jatkuu seuraavaan lähteeseen.

### Osittainen epäonnistuminen (RSS-syötteet)

RSS-job käy läpi kaikki lähteet. Jos yksi lähde epäonnistuu:

1. Virhe kirjataan Cloud Logging -lokiin strukturoituna JSON-merkintänä: `{source, url, error, status_code, attempt}`.
2. `last_fetched_at` päivitetään **vain onnistuneiden lähteiden osalta** (per-lähde-arvo `config`-taulussa).
3. Cloud Scheduler yrittää koko jobin uudelleen seuraavassa ajastuksessa normaalisti.

### BigQuery-kirjoitusvirhe

Jos MERGE-operaatio epäonnistuu:

1. Peruutetaan koko batch (ei osittaisia kirjoituksia).
2. `last_fetched_at` ei päivity.
3. Virhe kirjataan lokiin.
4. Palautetaan Cloud Run Job exit code `1` → Cloud Scheduler merkitsee ajon epäonnistuneeksi.

### Monitorointi

Cloud Logging -suodattimet jotka kannattaa asettaa hälytyksiksi:

```
resource.type="cloud_run_job"
severity="ERROR"
```

Cloud Monitoring -metriikka: `run.googleapis.com/job/completed_execution_count` jaoteltuna `result=failed`.

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
- #8 OG-scraper (rate limiting)
- #9 Design guidelines
- #10 Outbox-endpoint
- #11 Tykkäyslaskuri
- #12 `updated`-aikaleima
- #13 Delete-toiminto
