# Design Guidelines: gcs-activitystreams

Nämä periaatteet ohjaavat kaikkia arkkitehtuuripäätöksiä. Viittaa tähän dokumenttiin tikettejä kirjoittaessasi.

---

## Asiat riitelevät, ei ihmiset

Tässä palvelussa sisältö on toimijana, ei henkilö.

AS2-spesifikaatio tukee `actor`-kentässä `Person`-tyyppiä, mutta tässä projektissa actoreita ei käytetä käyttäjäidentiteetteinä. Kaikki `actor`-arvot ovat joko:

- `Organization` — julkaisija (Helsingin Sanomat, Helsingin kaupunki, HRI)
- `Service` — automaattipalvelu (Voikko-enrichment, RSS-jobi, OG-scraper)

**Käyttäjä voi kopioida sisältöä ja julkaista sen omanaan** `Create`-aktiviteetilla omalla actorillaan, mutta palvelu itse ei mallinna käyttäjäidentiteettejä eikä sosiaaligraafeja. Ei `followers`, ei `following`, ei `Person`-actoreita backendin omissa objekteissa.

**ActivityPub-federointi**: Tämä on tarkoituksella read-heavy-arkkitehtuuri. Server-to-server-protokollaa (Mastodon-yhteensopivuus) ei tavoitella. `outbox` on julkinen luettava syöte, ei federoitu inbox. WebFinger-endpointia ei toteuteta. Jos Actor-resursseja toteutetaan myöhemmin, se on erillinen päätös ja erillinen tiketti.

---

## Ei `Dislike`, ei `Undo`

Kun asiat riitelevät eikä ihmiset, ei ole tarvetta osoittaa epämieltymystä muita ihmisiä kohtaan. `Like` on sisällön kiinnostavuuden mittari, ei sosiaalisen hyväksynnän signaali.

| Aktiviteetti | Tila | Perustelu |
|---|---|---|
| `Like` | ✅ Toteutetaan | Käyttäjä merkitsee artikkelin/asian kiinnostavaksi |
| `Dislike` | ❌ Ei toteuteta | Ei tarvetta sosiaaliselle epämieltymykselle |
| `Undo Like` | ❌ Ei toteuteta | Like-laskuri voi vain kasvaa |
| `Delete` | ✅ Toteutetaan | Käyttäjä voi poistaa oman kommenttinsa tai artikkelinsa |
| `Announce` | ❌ Ei toteuteta | Käyttäjä julkaisee sisällön `Create`-aktiviteetilla, ei uudelleenjakaa |

---

## `published` = alkuperäinen julkaisuhetki julkaisijan palvelussa

AS2-speksin mukaan `published` on objektin luontihetki ja `updated` on muokkaushetki. `published` ei koskaan muutu objektin päivityksissä — se on historiallinen tosiasia.

| Lähde | `published` | `updated` |
|---|---|---|
| RSS-artikkeli | `<pubDate>` tai `<dc:date>` syötteessä | `<atom:updated>` jos saatavilla |
| OpenAhjo-päätös | Päätöksen alkuperäinen julkaisupäivä julkaisijan sivuilla | `metadata_modified` jos muuttunut |
| HRI-datasetti | `metadata_created` CKAN-vastauksessa | `metadata_modified` |
| OG-scrapattu sivu | `article:published_time` OG-tagista | `article:modified_time` |
| Fallback (ei metatietoa) | Scrape-hetki — merkitään epätarkkuudeksi lokiin | — |

---

## Ei Actor-resursseja tässä backendissä

ActivityPub edellyttää, että `actor`-URL palauttaa täydellisen Actor-objektin (`inbox`, `outbox`, `followers`, `following`). Tässä projektissa `actor`-kentät viittaavat organisaatioihin ja palveluihin, joilla ei ole omaa AS2-endpointia. WebFinger-endpointia ei toteuteta. Fediverse-yhteensopivuus ei ole tavoite.

---

## Kommenttiketjun syvyysrajoitus

Sallittu syvyys on tasan kaksi tasoa:

```
Article                        (taso 0 – thread_root)
  └── Comment                  (taso 1 – in_reply_to = Article)
       └── Reply               (taso 2 – in_reply_to = Comment)
```

Vastaukseen ei voi vastata. Kirjoituspalvelu hylkää yrityksen `400 Bad Request` -vastauksella.

---

## Outbox-sivutus: tagirelevanssi + opaakki kursori

### Järjestys

Outbox-sivu palauttaa objektit **tagirelevanssijärjestyksessä**, ei aikajärjestyksessä. Relevanssi on yksinkertainen osumien laskenta: kuinka monta clientin pyytämistä tageista löytyy objektin `tag`-kentästä. Tasatilanteessa järjestetään `published DESC, id ASC`.

Aikajärjestystä ei käytetä pääjärjestyksenä, koska saman aiheen uutiset eri lähteistä ovat tasavertaisia riippumatta siitä, kuka julkaisi ensin.

### Kursori

Koska järjestys ei perustu aikaleimaan, sivutuskursorina käytetään **opaakkia `cursor`-parametria**. Client ei koskaan muodosta kursoria itse eikä pure sen sisältöä — palvelin kirjoittaa sen `next`-linkkiin ja lukee sen seuraavalla pyynnöllä.

Kursori on base64url-enkoodattu offset-arvo. Tämä on luotettava BigQuery-toteutuksessa kunhan `ORDER BY` on deterministinen (relevanssi DESC, published DESC, id ASC) eikä muutu sivujen välillä.

```
cursor = base64url({ "offset": 50 })
```

### AS2-rakenne

`GET /ap/outbox` ilman sivutusparametreja palauttaa `OrderedCollection`-kokoelman kuvauksen: vain `totalItems` ja `first`-linkki. Varsinaisia objekteja ei palauteta.

`GET /ap/outbox?n=50` ja seuraavat `next`-linkit palauttavat `OrderedCollectionPage`-sivun.

Sivutus on **yksisuuntainen** (uusimmasta/relevanteimmasta alaspäin). `prev` on aina `null`. Alkuun palataan `first`-linkin kautta.

### Query-parametrit

| Parametri | Kuka asettaa | Kuvaus |
|---|---|---|
| `tag` | Client | Yksi tai useampi tagi (toistuva). `tag=asuminen&tag=helsinki` |
| `n` | Client | Sivun koko. Palvelin käyttää 50 oletuksena jos puuttuu. |
| `cursor` | Palvelin | Opaakki. Kirjoitetaan `next`-linkkiin. Client ei aseta tätä. |
| `after` | Client | Valinnainen aikaikkuna: vain tämän jälkeen julkaistut. |

`source`-parametria ei ole: client ei päätä mistä lähteestä dataa haetaan, vain mitä tageja seurataan.

---

## Liittyy

- Kaikki muut tiketit — nämä periaatteet ohjaavat kaikkia arkkitehtuuripäätöksiä
- #10 OrderedCollectionPage -sivutus
