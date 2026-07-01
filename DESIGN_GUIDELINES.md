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

## Outbox-sivutus: tagirelevanssi, ei kursoreja

### Järjestys

Outbox palauttaa objektit **tagirelevanssijärjestyksessä**, ei aikajärjestyksessä. Relevanssi on yksinkertainen osumien laskenta: kuinka monta clientin pyytämistä tageista löytyy objektin `tag`-kentästä. Tasatilanteessa järjestetään `like_count DESC, updated DESC, published DESC, id ASC`.

Aikajärjestystä ei käytetä pääjärjestyksenä, koska saman aiheen uutiset eri lähteistä ovat tasavertaisia riippumatta siitä, kuka julkaisi ensin.

### Malli

Client pyytää aina alusta `n` kappaletta. Ei kursoreja, ei sivunumeroita. Client huolehtii itse duplikaattisuodatuksesta `id`-kentän perusteella — jos client haluaa enemmän tuloksia, se pyytää suuremmalla `n`:llä.

```
GET /ap/outbox?tag=asuminen&n=50    → top-50
GET /ap/outbox?tag=asuminen&n=100   → top-100 (sisältää edellisen 50, client suodattaa)
GET /ap/outbox?tag=asuminen&n=150   → top-150
```

`OrderedCollectionPage`-tasoa ei tarvita — pelkkä `OrderedCollection` riittää.

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

Ei `next`, ei `prev`, ei `cursor`, ei `first`. `totalItems` kertoo clientille paljonko pyytää maksimissaan.

### Query-parametrit

| Parametri | Kuvaus |
|---|---|
| `tag` | Yksi tai useampi tagi (toistuva). Pakollinen — `400` jos puuttuu. |
| `n` | Haettavien objektien määrä. Oletus 50, maksimi 500. |
| `after` | Valinnainen aikaikkuna: vain tämän jälkeen julkaistut. |

`source`-parametria ei ole: client ei päätä mistä lähteestä dataa haetaan, vain mitä tageja seurataan.

---

## Liittyy

- Kaikki muut tiketit — nämä periaatteet ohjaavat kaikkia arkkitehtuuripäätöksiä
- #10 Outbox-endpoint
