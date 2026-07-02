# STANDARDS.md — gcs-activitystreams normatiiviset vaatimukset

> **Rajanveto:** Tämä tiedosto määrittää ulkoiset normatiiviset vaatimukset sellaisina kuin ne on julkaistu.
> Se **miten** ne toteutetaan tässä projektissa on [TECHNICAL_DESIGN.md](./TECHNICAL_DESIGN.md):ssä.
>
> Ristiin-viittaus: [patterns/STANDARDS.md](https://github.com/uutisseuranta/patterns/blob/main/STANDARDS.md) — frontend AS2-kenttäkartta.

---

## 1. W3C ActivityStreams 2.0

Spesifikaatio: [https://www.w3.org/TR/activitystreams-core/](https://www.w3.org/TR/activitystreams-core/)

### 1.1 Käytetyt kentät backend-vastauksessa

Backend palauttaa nämä AS2-kentät API-vastauksessa:

| Kenttä | Tyyppi | Pakollinen | Huomio |
|--------|--------|------------|--------|
| `@context` | IRI | Kyllä | Vakioarvo `"https://www.w3.org/ns/activitystreams"` |
| `id` | IRI | Kyllä | Absoluuttinen URI; kaava: `https://uutisseuranta.fi/articles/{source}/{sha256(url)}` |
| `type` | string | Kyllä | `Article`, `Note`, `Collection`, `OrderedCollection` |
| `name` | string | Kyllä | Artikkelin otsikko |
| `summary` | string | Suositeltu | Lyhyt kuvaus |
| `content` | HTML string | Suositeltu | Käyttää sistä HTML-merkkiä |
| `published` | xsd:dateTime | Kyllä | RFC 3339, UTC, Z-suffiksi |
| `attributedTo` | Objekti | Kyllä | Keväyt viittaus: `{type, id, name}` — ei Actor-endpointia |
| `tag` | Hashtag[] | Ei | Voikko-lemmat; `{type: "Hashtag", name: "#tag"}` |
| `replies` | Collection | Ei | Backend **ei** palauta valmista `replies`-objektia — frontend kokoaa itse (ks. [patterns#50](https://github.com/uutisseuranta/patterns/issues/50)) |
| `likes` | Collection | Ei | `{type: "Collection", totalItems: N}` |
| `shares` | Collection | Ei | Laskentalogiikka tarkistettava (ks. [patterns#50](https://github.com/uutisseuranta/patterns/issues/50)) |

### 1.2 Rajaukset

**Ei Actor-objekteja:** Tässä projektissa ei toteuteta ActivityPub Actor -objekteja (`Person`, `Group`, `Organization`, `Service`) täysinä Actor-endpointeina. `attributedTo` sisältää keväyt viittauksen (`type` + `id` + `name`) ilman erillistä Actor-profiilisivua tai Webfinger-hakua.

**Ei audience targeting -kenttiä:** `to`, `cc`, `bto`, `bcc` ja `audience`-kenttiä ei käytetä missään API-vastauksessa. Kaikki objektit oletetaan julkisiksi.

**Ensisijainen tavoite AS2-yhteensopivuus:** ActivityPub-laajennukset ovat mahdollisia myöhemmin erillisinä projekteina — dokumentoidaan silloin hallittuina divergensseina STANDARDS.md:hen.

---

## 2. RFC 3339 — Aikaleimakentät

Spesifikaatio: [https://datatracker.ietf.org/doc/html/rfc3339](https://datatracker.ietf.org/doc/html/rfc3339)

- Kaikki datetime-kentät **UTC**, **Z-suffiksi** pakollinen.
- Muoto: `YYYY-MM-DDTHH:MM:SSZ` (esim. `2026-07-02T14:00:00Z`).
- Ei aikavyöhykeoffsetteja (`+02:00` tms.) — kaikki UTC:ksi muunnettuna.
- Kentät joihin vaatimus koskee: `published`, `updated` kaikissa AS2-objekteissa.

---

## 3. GDPR — Henkilötietojen käsittely

Säädös: EU 2016/679, [https://eur-lex.europa.eu/eli/reg/2016/679/oj](https://eur-lex.europa.eu/eli/reg/2016/679/oj)

### Raakavastauksessa ei henkilötietoja

Backend ei palauta henkilötietoja raakana API-vastauksessa:
- Ei sähköpostiosoitteita
- Ei IP-osoitteita
- Ei tunnistettavia käyttäjäprofiilidataa

### Anonymisointi- ja poistovaatimukset

- Anonymisointi: toteutus määritelty erikseen (ks. [#37](https://github.com/uutisseuranta/gcs-activitystreams/issues/37))
- Poisto-oikeus (right to erasure, Art. 17): toteutus määritelty erikseen (ks. [#37](https://github.com/uutisseuranta/gcs-activitystreams/issues/37))
- Tietojen säilytysaika: määriteltävä ennen tuotantokäyttöä

---

## Viitteet

- W3C ActivityStreams 2.0: [https://www.w3.org/TR/activitystreams-core/](https://www.w3.org/TR/activitystreams-core/)
- W3C ActivityStreams 2.0 Vocabulary: [https://www.w3.org/TR/activitystreams-vocabulary/](https://www.w3.org/TR/activitystreams-vocabulary/)
- RFC 3339: [https://datatracker.ietf.org/doc/html/rfc3339](https://datatracker.ietf.org/doc/html/rfc3339)
- GDPR: [https://eur-lex.europa.eu/eli/reg/2016/679/oj](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- Frontend kenttäkartta: [patterns/STANDARDS.md](https://github.com/uutisseuranta/patterns/blob/main/STANDARDS.md)
