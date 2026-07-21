# Toteutussuunnitelma — bq-activitystreams

Kukin label vastaa yhtä PR:ää. Issuet on ryhmitelty labeleittain toteutusjärjestyksessä.
Merkintä `→` tarkoittaa riippuvuutta: edellinen PR on oltava mergettynä ensin.

---

## Label: `0-sprint` — Välitön (blokkaajat, tehdään ensin)

| Issue | Otsikko | Huomio |
|---|---|---|
| [#21](https://github.com/uutisseuranta/bq-activitystreams/issues/21) | Testien käyttöönotto: unit-test.sh + smoke-test.yml | CI-pipeline puuttuu — ensimmäinen asia kuntoon |
| [#43](https://github.com/uutisseuranta/bq-activitystreams/issues/43) | chore: uudelleennimeä gcs-activitystreams → bq-activitystreams | Repo-nimi korjataan kaikissa viitteissä |

**PR-jako:**
- PR `0-sprint/ci-pipeline` — issue #21 (unit-tests.yml + WIF-konfiguraatio)
- PR `0-sprint/rename-repo-refs` — issue #43 (README + LICENSES.md + TECHNICAL_DESIGN.md viittaukset)

---

## Label: `mvp` — Alpha-julkaisun ydinominaisuudet

Järjestys on tiukka: RSS-jobi ensin, sillä muut riippuvat samasta datapipelinesta.

| Issue | Otsikko | Riippuu |
|---|---|---|
| [#17](https://github.com/uutisseuranta/bq-activitystreams/issues/17) | Cloud Run: structured logging + liveness/readiness-probet | — |
| [#50](https://github.com/uutisseuranta/bq-activitystreams/issues/50) | chore: katselmoi ja yhtenäistä HTTP-virhekoodikäytännöt | — |
| [#32](https://github.com/uutisseuranta/bq-activitystreams/issues/32) | infra: Monivaiheinen Dockerfile libvoikko-tuella | — |
| [#3](https://github.com/uutisseuranta/bq-activitystreams/issues/3) | Cloud Run Job: Ahjo-päätökset AS2-objekteina BigQueryhyn | → #31 (Ahjo API -migraatio selvitettävä ensin) |
| [#31](https://github.com/uutisseuranta/bq-activitystreams/issues/31) | Arkkitehtuuri: OpenAhjo API korvaaminen uudella Ahjo REST API:lla | tehdään ennen #3 |
| [#4](https://github.com/uutisseuranta/bq-activitystreams/issues/4) | Cloud Run Job: HRI avoimen datan metatiedot CKAN API:sta | → #3 valmis (yhteinen AS2-kirjasto) |
| [#13](https://github.com/uutisseuranta/bq-activitystreams/issues/13) | Cloud Run: Delete-aktiviteetti — kommenttien poisto | → write-api toimii |

**PR-jako:**
- PR `mvp/logging-probes` — issuet #17 + #50 (logging + virhekoodikäytännöt yhdessä)
- PR `mvp/dockerfile-voikko` — issue #32 (multi-stage Dockerfile)
- PR `mvp/ahjo-api-migrate` — issue #31 (Ahjo API -selvitys + dokumentaatio)
- PR `mvp/ahjo-job` — issue #3 (Ahjo Cloud Run Job)
- PR `mvp/hri-job` — issue #4 (HRI Cloud Run Job)
- PR `mvp/delete-activity` — issue #13 (Delete-aktiviteetti write-api:ssa)

---

## Label: `gdpr` — GDPR-vaatimukset (lakisääteinen, ennen julkaisua)

Voidaan tehdä rinnakkain `mvp`-työn kanssa.

| Issue | Otsikko | Riippuu |
|---|---|---|
| [#37](https://github.com/uutisseuranta/bq-activitystreams/issues/37) | feat: GDPR — käyttäjän sosiaalisen datan poisto ja anonymisointi | → uutisseuranta.github.io #49 + #50 (frontend pyytää poistoa) |

**PR-scope:** PR `gdpr/user-data-deletion` — issue #37 (`POST /ap/users/delete` + BigQuery-siivous).
Koordinoitava uutisseuranta.github.io `gdpr/account-deletion` -PR:n kanssa.

---

## Label: `hardened` — Tietoturvakovennukset (ennen tuotantoa)

Nämä tehdään `mvp`-työn jälkeen.

| Issue | Otsikko | Huomio |
|---|---|---|
| [#59](https://github.com/uutisseuranta/bq-activitystreams/issues/59) | sec: rate limiting — /ap/outbox + /ap/activities + /ap/scrape | Vaihtoehto B (slowapi) suositeltu MVP:ssä |
| [#41](https://github.com/uutisseuranta/bq-activitystreams/issues/41) | sec: DevSecOps-pipelinejen suunnittelu ja käyttöönotto | Kattaa Bandit, Dependabot, Trivy, OWASP ZAP |
| [#45](https://github.com/uutisseuranta/bq-activitystreams/issues/45) | sec: lisää Dependabot Python-riippuvuuksille | Osa #41:n kokonaisuutta |

**PR-jako:**
- PR `hardened/rate-limiting` — issue #59 (`slowapi` + 429-vastaukset + testit)
- PR `hardened/devsecops` — issuet #41 + #45 (Dependabot + Trivy + OWASP ZAP + Bandit Ruff-säännöt)

---

## Label: `AS2` — ActivityStreams 2.0 -yhteensopivuus

Nämä voidaan tehdä rinnakkain muiden töiden kanssa. Cross-repo-issuet koordinoitava patterns-repon kanssa.

| Issue | Otsikko | Riippuu |
|---|---|---|
| [#35](https://github.com/uutisseuranta/bq-activitystreams/issues/35) | feat: Content Negotiation — sama IRI palauttaa HTML tai AS2 JSON-LD | — |
| [#33](https://github.com/uutisseuranta/bq-activitystreams/issues/33) | feat: vastaanota Like/Dislike + summaa Agree/Disagree-laskurit | → #48 (Dislike BQ-migraatio) |
| [#48](https://github.com/uutisseuranta/bq-activitystreams/issues/48) | feat: BigQuery-migraatio Dislike-aktiviteeteille | tehdään ennen #33 |
| [#54](https://github.com/uutisseuranta/bq-activitystreams/issues/54) | Meta: Cross-repo AS2 contract — objektimalli, MIME-tyypit, JSON-LD-konteksti | koordinoi patterns + frontend |
| [#53](https://github.com/uutisseuranta/bq-activitystreams/issues/53) | Testing: AS2 cross-repo compatibility test harness | → #54 contract määritelty |

**PR-jako:**
- PR `as2/content-negotiation` — issue #35 (Accept-header -reititys query-api:ssa)
- PR `as2/dislike-migration` — issue #48 (BQ-taulu `dislikes`)
- PR `as2/like-dislike-handlers` — issue #33 (Like/Dislike-käsittelijat + toggle-logiikka)
- PR `as2/contract-meta` — issue #54 (dokumentaatio, koordinoi cross-repo)
- PR `as2/contract-tests` — issue #53 (`test_as2_contract.py`)

---

## Label: `testing` — Testikattavuus

Nämä voidaan tehdä rinnakkain ominaisuustöiden kanssa. Suositus: kirjoita testit samaan PR:iin vastaavan ominaisuuden kanssa.

| Issue | Otsikko | Tehdään yhdessä |
|---|---|---|
| [#28](https://github.com/uutisseuranta/bq-activitystreams/issues/28) | Testing: laajenna write-api:n testejä (Create, Like, Update) | `as2/like-dislike-handlers` + `mvp/delete-activity` |
| [#29](https://github.com/uutisseuranta/bq-activitystreams/issues/29) | Testing: lisää yksikkötestit query-api:lle | `as2/content-negotiation` |
| [#27](https://github.com/uutisseuranta/bq-activitystreams/issues/27) | Testing: poista koodiduplikaatio unit-test.sh:sta | `0-sprint/ci-pipeline` |
| [#30](https://github.com/uutisseuranta/bq-activitystreams/issues/30) | Testing: lisää testit og-scraperille ja og-enrichment-jobille | erillinen PR |

**Periaate:** testit kuuluvat samaan PR:iin kuin ominaisuus. Erillistä `testing`-labeltettua PR:ää käytetään vain `#30` (og-scraper), jolla ei ole muuta kotia.

---

## Label: `enhancement` — Tuotantotason lisäominaisuudet (post-alpha)

Näitä ei tarvita alpha-julkaisuun. Tehdään kun alpha on stabiili.

| Issue | Otsikko | Riippuu |
|---|---|---|
| [#56](https://github.com/uutisseuranta/bq-activitystreams/issues/56) | perf: BigQuery-kuluoptimointi — materialisoitujen näkymien hyödynmäinen | — |
| [#55](https://github.com/uutisseuranta/bq-activitystreams/issues/55) | feat: BigQuery-käyttäjätilastorajapinta (like/dislike/comment_count) | → #48 Dislike-migraatio |
| [#36](https://github.com/uutisseuranta/bq-activitystreams/issues/36) | feat: /ap/users/{id}/stats — käyttäjäkohtaiset reaktiotilastot | → #33 Like/Dislike-käsittelijat |
| [#26](https://github.com/uutisseuranta/bq-activitystreams/issues/26) | feat: Wayback Machine SPN2 — arkistointilinkki AS2-objektiin | → write-api toimii |
| [#24](https://github.com/uutisseuranta/bq-activitystreams/issues/24) | feat: OG-rikastus RSS-artikkeleille (og-enrichment-job) | — |
| [#18](https://github.com/uutisseuranta/bq-activitystreams/issues/18) | feat: objects_pending-taulu skeema + rikastusjob (RSS ilman pubDate) | → #24 |

**PR-jako:**
- PR `perf/bq-materialized-views` — issue #56
- PR `feat/user-stats` — issuet #55 + #36 (käyttäjätilastot, sama endpoint)
- PR `feat/wayback-archive` — issue #26
- PR `feat/og-enrichment` — issue #24
- PR `feat/objects-pending` — issue #18

---

## Label: `documentation` — Dokumentaatio ja tekninen velka

| Issue | Otsikko | Huomio |
|---|---|---|
| [#52](https://github.com/uutisseuranta/bq-activitystreams/issues/52) | Meta: Jira–GitHub-integraation päätökset | Vain dokumentaatiota |
| [#46](https://github.com/uutisseuranta/bq-activitystreams/issues/46) | chore: populoi LICENSES.md toteutuksissa käytettyjen komponenttien perusteella | Tehdään `0-sprint/rename-repo-refs` jälkeen |
| [#15](https://github.com/uutisseuranta/bq-activitystreams/issues/15) | Lisensointimerkintä: avoimen datan käyttöehdot API-vastauksiin | Ennen laajaa julkaisua |

**PR-jako:**
- PR `docs/jira-meta` — issue #52 (ei koodimuutoksia)
- PR `docs/licenses` — issuet #46 + #15 (LICENSES.md + API-lisensointimerkinnät)

---

## Yhteenveto: PR-järjestys

```
0-sprint/ci-pipeline
0-sprint/rename-repo-refs

mvp/logging-probes
mvp/dockerfile-voikko
mvp/ahjo-api-migrate
  → mvp/ahjo-job
      → mvp/hri-job
mvp/delete-activity

gdpr/user-data-deletion     (rinnakkain mvp-työn kanssa)

as2/content-negotiation     (rinnakkain mvp-työn kanssa)
as2/dislike-migration
  → as2/like-dislike-handlers
as2/contract-meta
  → as2/contract-tests

hardened/rate-limiting      (mvp valmis ensin)
hardened/devsecops          (mvp valmis ensin)

perf/bq-materialized-views  (alpha stabiili ensin)
feat/user-stats             (alpha + #33 valmis)
feat/wayback-archive        (alpha stabiili ensin)
feat/og-enrichment          (alpha stabiili ensin)
feat/objects-pending        (og-enrichment valmis)

docs/*                      (missä vaiheessa tahansa)
```

---

## Puuttuvat issuet — avattava ennen toteutusta

| Aihe | Label | Mihin PR |
|---|---|---|
| Structured logging jaettu moduuli kaikille jobeille | `mvp` | lisätään `mvp/logging-probes`-PR:n scopeen |
| WCAG AA -vaatimukset API-virheviestien ihmisluettavuudelle | `hardened` | `hardened/rate-limiting` |
| Lisenssitarkistus: RSS-lähteiden käyttöehdot (HS, IS, IL, KL, MTV) | `documentation` | `docs/licenses` |
