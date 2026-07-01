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

  activitystreams.objects    ← jobit #2 (RSS), #3 (Ahjo), #4 (HRI), #8 (OG-scraper)
                                suora MERGE-kirjoitus, tavallinen taulu
                                tags-sarakkeen omistaja: Voikko-job (#6)
                                like_count-sarakkeen omistaja: likes-and-updated-job (#11/#12)

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
| **GCP-projekti** | `uutisseuranta-activitystreams` |
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
| `likes-and-updated-job` | `*/15 * * * *` | 15 min välein |

Huom: `likes-and-updated-job` korvaa aiemmat erilliset `likes-sync-job` ja `activity-updated-job` -ajastukset. Molemmat laskennat ajetaan samassa Cloud Run Job -suorituksessa — ks. [Tykkäyslaskuri ja updated-aikaleima](#tykkäyslaskuri-ja-updated-aikaleima-1112).

> [!NOTE]
> **Kesäaika:** Cloud Scheduler käyttää UTC:tä eikä säädä kesäaikaa automaattisesti. EET (talviaika UTC+2) ja EEST (kesäaika UTC+3) eroavat tunnin. Cron-kommentit on merkitty EET-ajassa — kesällä ajastukset lähtevät tunnin myöhemmin kuin kommentti ilmoittaa. Tämä on hyväksytty trade-off: ajastusajalla ei ole kriittistä merkitystä tässä kontekstissa.

---

## Autentikaatio ja valtuutus

| Palvelu | Autentikaatio | Perustelu |
|---|---|---|
| `GET /ap/outbox` | Julkinen, ei autentikointia | Avoin data — sama periaate kuin RSS-syötteet |
| `POST /ap/scrape` | Julkinen, ei autentikointia | Avoin data — käyttäjä osoittaa julkisen URL:n scrapattavaksi |
| `POST /ap/activities` | Cloud IAM `roles/run.invoker` | Kirjoitusoperaatio — vain valtuutetut palvelutilit voivat kutsua |

Kaikki kolme palvelua ovat erillisiä Cloud Run -palveluja. `og-scraper` on julkinen kirjoitusendpoint (tallentaa BigQueryyn), mutta koska data on avointa eikä käyttäjäkohtaista, autentikointia ei vaadita — kirjoitusoikeus BigQueryyn on Cloud Run -palvelun palvelutilillä IAM-oikeuksilla, ei kutsujalla.

---

## BigQuery-skeema (#1)

### `activitystreams.objects` — artikkelit, päätökset, datasetit

```sql
CREATE TABLE activitystreams.objects (
  id              STRING    NOT NULL OPTIONS(description='AS2 id – domain-pohjainen IRI, primääriavain'),
  source          STRING    NOT NULL OPTIONS(description='Lähde: rss | ahjo | hri | scraped | user'),
  published       TIMESTAMP NOT NULL OPTIONS(description='AS2 published – pakollinen, taulu on partitionoitu tämän mukaan'),
  updated         TIMESTAMP          OPTIONS(description='AS2 updated – päivittyy käyttäjäaktiivisuudesta (#12)'),
  tags            ARRAY<STRING>      OPTIONS(description='Lemmatisoidut tagit (Voikko #6)'),
  tags_enriched   BOOL      NOT NULL OPTIONS(description='TRUE kun Voikko-job on käsitellyt rivin — estää ikuisen uudelleenkäsittelysilmukan tyhjän tuloksen tapauksessa'),
  like_count      INT64     NOT NULL OPTIONS(description='Tykkäysmäärä activitystreams.likes-taulusta, päivitetään likes-and-updated-jobilla (#11/#12)'),
  deleted         BOOL      NOT NULL OPTIONS(description='Pehmeä poisto'),
  object_json     JSON               OPTIONS(description='Koko AS2-objekti natiivina JSON-tyypinä')
)
PARTITION BY DATE(published)
CLUSTER BY source, published;
```

> **Oletusarvot:** `tags_enriched=FALSE`, `like_count=0`, `deleted=FALSE` asetetaan INSERT-lauseissa, ei DDL:ssä. BigQuery CREATE TABLE ei tue DEFAULT-lausekkeita.

> **Miksi `published` on NOT NULL?**
> Taulu on partitionoitu `DATE(published)`-sarakkeen mukaan. Ilman `published`-arvoa rivi ei partitionoidu oikein ja queryt hidastuvat merkittävästi. RSS-job ohittaa artikkelit joilta `<pubDate>` puuttuu (ks. #2). OG-scraper tallentaa `published = scrape-hetki` fallbackina. Poikkeustapaukset käsitellään erillisessä prosessissa (#14).

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
| Käyttäjän luoma | `https://activitystreams.uutisseuranta.net/ap/objects/user/{google-sub}/{ulid}` |

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
        -- tags, tags_enriched, like_count ja deleted jätetään tarkoituksella pois
WHEN NOT MATCHED THEN
    INSERT (id, source, published, updated, tags, tags_enriched, like_count, deleted, object_json)
    VALUES (S.id, S.source, S.published, S.updated, [], FALSE, 0, FALSE, S.object_json)
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

**Analyysi:** `libvoikko` palauttaa sanan `BASEFORM`-lemman. Käytetyt sanaluokat: `nimisana`, `erisnimi`, `paikannimi`, `paikannimi_ulkomaat`, `teonsana`, `määrite`. Hylätään: `suhdesana`, `sidesana`, `kieltosana`, `asemosana`, `partikkeli`.

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

## Cloud Run endpoint: OG-scraper (#8)

`POST /ap/scrape { "url": "https://..." }` — palauttaa AS2 `Article`-objektin ja tallentaa sen `objects`-tauluun.

Endpoint on julkinen (ks. [Autentikaatio ja valtuutus](#autentikaatio-ja-valtuutus)). Kirjoitusoikeus BigQueryyn on Cloud Run -palvelun palvelutilillä IAM-oikeuksilla, ei kutsujalla.

**Domain-whitelist:** Scraper hyväksyy vain manuaalisesti ylläpidetyn domain-listan URL:t. Uusi domain lisätään listaan harkiten ja testataan ennen käyttöönottoa. Tuntemattomat domainit palauttavat `403 Forbidden`. Tämä estää SSRF-hyväksikäytöt ja tahattoman ulkopuolisten palvelinten kuormittamisen.

**Duplikaattipyynnöt:** Sama URL kahdesti tuottaa saman `id`:n (`sha256(url)`), joten MERGE-operaatio hoitaa duplikaatin hiljaisesti.

**Kenttäkartoitus** (ks. myös [`id`-kentän kaava](#id-kentän-kaava-lähteittäin)):

| AS2-kenttä | OG-lähde | Fallback |
|---|---|---|
| `name` | `og:title` | `<title>` |
| `summary` | `og:description` | `<meta name=description>` |
| `image` | `og:image` | — |
| `published` | `article:published_time` | Scrape-hetki |
| `updated` | `article:modified_time` | Scrape-hetki |

`published` käyttää scrape-hetkeä fallbackina (toisin kuin RSS-job, joka ohittaa artikkelin). Tämä on hyväksyttävää koska OG-scraper on käyttäjän manuaalisesti käynnistämä toiminto.

---

## Cloud Run: kirjoituspalvelu (#7)

Vastaanottaa AS2-aktiviteetteja JSON:na. Validoi tyypin ja pakolliset kentät. Kirjoittaa `activities`- ja tarvittaessa `likes`-tauluun BigQuery Storage Write API:lla.

Autentikaatio: ks. [Autentikaatio ja valtuutus](#autentikaatio-ja-valtuutus).

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

Delete ei poista tietokannasta. `activities`-tauluun kirjataan `Delete`-tapahtuma ja `objects.deleted` asetetaan `TRUE`:ksi. Poistettu kommentti näytetään paikkamerkkinä `[kommentti poistettu]` jos sillä on vastauksia.

### Update-semantiikka

Käyttäjä voi päivittää **vain oman kommenttinsa** sisällön. Organisaatioiden julkaisemia objekteja (RSS, Ahjo, HRI, OG-scraped) ei voi päivittää `Update`-aktiviteetilla — ne ovat jobien omistamia.

- `actor` validoidaan: `Update`-pyynnön `actor` täytyy vastata alkuperäisen `Create`-aktiviteetin `actor`-arvoa. Jos ne eivät täsmää, palautetaan `403 Forbidden`.
- `published` ei muutu — vain `object_json` ja `updated` päivittyvät.
- `Update`-aktiviteetti kirjoitetaan `activities`-tauluun ja `objects.object_json` päivitetään MERGE-operaatiolla.
- `thread_root`-artikkelin `updated` päivittyy muokatusta kommentista (ks. [Tykkäyslaskuri ja updated-aikaleima](#tykkäyslaskuri-ja-updated-aikaleima-1112)).

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
  published     DESC NULLS LAST, -- alkuperäinen julkaisu
  id            ASC
```

### Query-parametrit

| Parametri | Kuvaus | Oletus | Maksimi |
|---|---|---|---|
| `tag` | Toistuva. Pakollinen — `400` jos puuttuu. | — | — |
| `n` | Palautettavien määrä. Yli 500 → `400`. | 50 | 500 |

### `totalItems`-caching

`COUNT(*)`-kysely cachetetaan Cloud Run -muistissa 5 minuutiksi tag-kombinaatiota kohden. Cache on instanssikohtainen — `totalItems` on approksimatiivinen arvo. Caching vähentää merkittävästi BigQuery-kustannuksia: COUNT(*) ei aja per pyyntö, vain 5 minuutin välein.

---

## Tykkäyslaskuri ja updated-aikaleima (#11/#12)

`likes-and-updated-job` ajaa 15 minuutin välein kaksi laskentaa peräkkäin samassa Cloud Run Job -suorituksessa. Nämä olivat aiemmin erilliset jobit; ne on yhdistetty koska molemmat kirjoittavat samaan `objects`-tauluun samalla 15 min rytmillä. Virheenkäsittely: ks. [Virheenkäsittely ja retry-logiikka](#virheenkäsittely-ja-retry-logiikka).

### Vaihe 1: like_count-päivitys

`like_count INT64 NOT NULL DEFAULT 0` on suoraan `activitystreams.objects`-taulussa.

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

- Laskuri voi vain **kasvaa** — `Undo Like` ei ole mahdollinen (ks. [Miksi `Undo` ei ole tuettu?](#tuetut-aktiviteetit))
- Laskuri on anonyymi — ei tietoa kuka tykkäsi
- `deleted = FALSE` -ehto estää poistettujen objektien päivittymisen

### Vaihe 2: updated-aikaleiman päivitys

Kun artikkeliin kohdistuu käyttäjäaktiivisuutta, **thread_root-artikkelin** `updated` päivittyy. Poistetut aktiviteetit suodatetaan `object_id`:n perusteella: jos `activities`-taulussa on `type = 'Delete'` samalla `object_id`:llä, kyseinen aktiviteetti jätetään huomiotta.

| Tapahtuma | Päivittää `updated` |
|---|---|
| Artikkelin sisältö muuttuu | ✅ |
| Käyttäjä kommentoi artikkelia | ✅ |
| Käyttäjä tykkää artikkelista | ✅ |
| Käyttäjä kommentoi kommenttia | ✅ (thread_root) |
| Käyttäjä tykkää kommentista | ✅ (thread_root) |

```sql
SELECT COALESCE(thread_root, object_id) AS root_url,
       MAX(published) AS last_activity_at
FROM activitystreams.activities a
WHERE type IN ('Like', 'Create')
  AND NOT EXISTS (
    SELECT 1 FROM activitystreams.activities d
    WHERE d.type = 'Delete' AND d.object_id = a.object_id
  )
GROUP BY root_url
```

`COALESCE(thread_root, object_id)` varmistaa että artikkeliin suoraan kohdistuvat tykkäykset (joilla `thread_root = NULL`) käsitellään oikein. `updated` ei koskaan kulje taaksepäin (`AND @last_activity_at > updated`).

> [!NOTE]
> **Poistetun artikkelin kommentit:** Jos artikkeli poistetaan mutta sen kommenteille ei kirjata omaa `Delete`-aktiviteettia, näiden kommenttien `Like`-tapahtumat voivat edelleen kasvattaa `updated`-aikaleimaa. Kommentit voivat jäädä elämään omaa elämäänsä poistetun artikkelin jälkeen. Tämä on hyväksytty trade-off: vaikutus on kosmeettinen, koska poistettu artikkeli ei palaudu outbox-hauissa (`WHERE deleted = FALSE`).

Avoimen datan BigQueryyn kirjoitetaan vain aikaleima — ei tietoa kuka kommentoi tai tykkäsi.

### `likes`-kenttä AS2-vastauksessa

Query-API upottaa `like_count`-arvon palautushetkellä suoraan AS2-objektiin:

```json
{
  "type": "Article",
  "id": "https://activitystreams.uutisseuranta.net/ap/objects/articles/hs/abc123",
  "name": "Artikkelin otsikko",
  "likes": 42
}
```

`object_json`-sarakkeeseen ei tallenneta `likes`-kenttää pysyvästi — se lasketaan lennossa.

---

## RSS-syötteet ilman pubDate (#14)

RSS-syötteet joilta `<pubDate>` puuttuu rakenteellisesti jäävät nykyisellä logiikailla indeksoimatta. Ratkaisu (toteutetaan tarvittaessa — ks. #18):

1. RSS-job tallentaa nämä artikkelit väliaikaiseen varastoon (`activitystreams.objects_pending`)
2. Erillinen rikastusjob hakee `published`-arvon muista lähteistä: OG-scraper (#8), HTTP `Last-Modified`, JSON-LD `datePublished`
3. Kun `published` on selvitetty, artikkeli siirretään normaaliin `objects`-tauluun
4. Jos `published` ei selviä 7 päivässä, artikkeli hävitetään välivarastosta

Prioriteetti: **matala** — toteutetaan vasta kun jokin RSS-lähde oikeasti aiheuttaa ongelman. Skeema: ks. #18.

---

## Virheenkäsittely ja retry-logiikka

Kaikissa Cloud Run Job -ajoissa noudatetaan yhtenäistä virheenkäsittelymallia.

### `last_fetched_at` — päivitetään vain onnistuneesta ajosta

`config`-taulun `last_fetched_at`-arvo päivitetään **ainoastaan kun koko ajo on suoritettu onnistuneesti**. Epäonnistunut ajo ei päivitä arvoa — seuraava ajo käyttää edellistä onnistunutta ajankohtaa fetch-ikkunana, jolloin yksikään artikkeli ei jää väliin.

### Retry-strategia HTTP-virheille

| HTTP-statuskoodi | Toiminto |
|---|---|
| `2xx` | Jatketaan normaalisti |
| `429 Too Many Requests` | Odotetaan `Retry-After`-otsakkeen mukainen aika (tai 60 s), max 3 yritystä |
| `5xx` | Eksponentiaalinen backoff: 30 s, 5 min, 15 min. Max 3 yritystä. |
| `404 Not Found` | Kirjataan lokiin, jatketaan muiden lähteiden käsittelyä. Ei retrytä. |
| Connection error / timeout | Sama kuin `5xx`. |

RSS-job ei kaada koko ajoa yksittäisen lähteen virheestä — virhe kirjataan lokiin, ajo jatkuu seuraavaan lähteeseen.

### BigQuery-kirjoitusvirhe

Jos MERGE-operaatio epäonnistuu: koko batch peruutetaan, `last_fetched_at` ei päivy, virhe kirjataan lokiin, Cloud Run Job palauttaa exit code `1`.

---

## Kustannusarvio

| Resurssi | Arvio | Hinta |
|---|---|---|
| BigQuery kyselyt (100k riviä, n=500) | ~110 MB/pyyntö | ~$0.0007/pyyntö |
| Ilmainen 1 TB/kk -kiintiö | ~9 000 pyyntöä 100k rivillä | $0/kk |
| `totalItems` COUNT(*) | Cachetettu 5 min — ei aja per pyyntö | Merkittävä säästö |
| Cloud Run Job -suoritukset | <30s/ajo | $0/kk (ilmainen taso) |
| Cloud Run palvelu | ~1000 pyyntöä/kk | $0/kk (ilmainen taso) |

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
- #9 published/updated-kenttien logiikka
- #10 Outbox-endpoint
- #11 Tykkäyslaskuri
- #12 `updated`-aikaleima
- #14 RSS-syötteet ilman pubDate
- #15 Lisensointimerkintä
- #16 Cloud Run ympäristömuuttujat
- #17 Logging ja monitoring
- #18 objects_pending-skeema
