# LICENSES.md — Avointen komponenttien lisenssit ja ylläpito (gcs-activitystreams)

Tämä tiedosto kuvaa **gcs-activitystreams**-repositoriossa käytetyt avoimen lähdekoodin kolmannen osapuolen ohjelmistokomponentit, niiden lisenssit, ylläpidon tilan, vastuutahot ja ylläpitäjien ensisijaiset toimintamaat.

| Komponentti | Lisenssi | Ylläpidon tila | Vastuutaho | Ylläpitäjän maa |
| :--- | :--- | :--- | :--- | :--- |
| **FastAPI** | MIT | Erittäin aktiivinen | Sebastián Ramírez (tiangolo) / Core-tiimi | 🇩🇪 Saksa / 🇨🇴 Kolumbia |
| **Uvicorn** | BSD-3-Clause | Erittäin aktiivinen | Encode / Tom Christie & tiimi | 🇬🇧 Iso-Britannia |
| **google-cloud-bigquery** | Apache-2.0 | Erittäin aktiivinen | Google LLC / Google Cloud | 🇺🇸 Yhdysvallat |
| **google-auth** | Apache-2.0 | Erittäin aktiivinen | Google LLC / Google Cloud | 🇺🇸 Yhdysvallat |
| **libvoikko** | GPL-3.0-or-later | Vakaa (ylläpitotila) | Harri Pitkänen & Voikko-yhteisö | 🇫🇮 Suomi |
| **beautifulsoup4** | MIT | Aktiivinen ylläpito | Leonard Richardson | 🇺🇸 Yhdysvallat |
| **httpx** | BSD-3-Clause | Erittäin aktiivinen | Encode / Tom Christie & tiimi | 🇬🇧 Iso-Britannia |
| **lxml** | BSD-3-Clause / GPL | Vakaa ylläpito | lxml-projekti / Stefan Behnel | 🇩🇪 Saksa |
| **requests** | Apache-2.0 | Aktiivinen ylläpito | Kenneth Reitz / Python Software Foundation | 🇺🇸 Yhdysvallat / Globaali |
| **ulid-py** | MIT | Ylläpitotila | Andrew Hawker | 🇺🇸 Yhdysvallat |

---

## Komponenttien yksityiskohtainen kuvaus

### 1. FastAPI / Uvicorn / httpx / requests
- **Rooli:** API-rajapinnan rakentaminen, asynkroninen web-palvelin ja HTTP-pyynnöt ulkoisiin lähteisiin (esim. RSS-syötteet ja Open Graph -haku).
- **Elinvoima:** Erittäin vahva. FastAPI ja Uvicorn ovat nykyaikaisen Python-webkehityksen ytimessä.
- **Maantiede:** Pääylläpitäjä Sebastián Ramírez asuu Saksassa (syntyjään Kolumbiasta). Encode-ryhmä toimii pääosin Isossa-Britanniassa.

### 2. Google Cloud SDK (BigQuery & Auth)
- **Rooli:** BigQuery-integraatiot, tietojen tallennus ja OIDC-tunnisteiden varmennus kirjoitusoikeuksia varten.
- **Elinvoima:** Erittäin vahva. Google ylläpitää ja kehittää SDK-kirjastoja jatkuvasti osana GCP-ekosysteemiä.
- **Maantiede:** Yhdysvallat.

### 3. libvoikko
- **Rooli:** Suomen kielen sanojen morfologinen analyysi ja hakusanojen jäsennys dataputkessa (`voikko_job`).
- **Elinvoima:** Kypsä ja vakaa. Päivityksiä tulee harvoin, mutta kirjasto on ainoa laatuaan suomen kielen avoimessa käsittelyssä.
- **Maantiede:** Suomi.

### 4. lxml / BeautifulSoup4
- **Rooli:** HTML/XML-syötteiden jäsennys ja uutisobjektien tietojen (esim. RSS ja Open Graph) kaavinta.
- **Elinvoima:** Vakaa ja vakiintunut. Molemmat kirjastot ovat olleet Python-ekosysteemin peruskiviä jo yli kymmenen vuotta.
- **Maantiede:** Saksa / Yhdysvallat.
