# GDPR-kontraktet — vad som får lämna systemet, och varför

> **Varför denna fil finns.** `failures.md` är kontraktet för vad som händer
> när något går fel. Den här filen är kontraktet för vad som händer när något
> går *rätt* — när data samlas in, berikas, indexeras och skickas vidare. Ett
> AI-system som läser dokument, foton och e-post rör personuppgifter i varje
> steg, och de flesta integritetsfel är inte buggar: de är **funktioner som
> gör precis vad de ska**, på data de inte borde ha fått.
>
> Regeln är densamma som i `failures.md`: **en princip per område, så att en
> host vet vad som gäller utan att läsa kod.** Där `failures.md` frågar *vem
> äger felet?* frågar den här filen *vem äger uppgiften?*

---

## 1. Grundprincipen

**Personuppgifter är inte ett fält — det är en egenskap hos ett flöde.**

Samma sträng kan vara harmlös i en kontext och en personuppgift i en annan.
`"Anders"` i en projekttitel är ett namn på en kollega. `"Anders"` i ett
sjukintyg är en hälsouppgift (Art. 9). Systemet kan inte avgöra skillnaden
genom att titta på strängen — bara genom att veta **var den kom ifrån och
vem som får se den.**

Därför är den enda hållbara arkitekturen: **ägarskap sätts vid ingången, och
varje läsning filtrerar på det.** Inte: "vi städar bort känsliga fält i
efterhand".

| Lager | Fråga | Svar |
|---|---|---|
| **Insamling** | Får vi ha uppgiften? | Rättslig grund (Art. 6), och för Art. 9 en explicit undantagsregel |
| **Lagring** | Var bor den? | I sin källa — aldrig kopierad till ett vidare fält |
| **Indexering** | Blir den sökbar? | Bara inom sitt ägarskap; aldrig i en global pool |
| **Vidarebefordran** | Lämnar den systemet? | Bara till en provider med avtal, och bara det som behövs |
| **Gallring** | Försvinner den? | När källan försvinner — inte när någon kommer ihåg |

**Det vanligaste felet är inte läckage. Det är spegling.** Ett fält kopieras
till ett index, ett koncept, en sammanfattning eller en embedding — och
plötsligt finns uppgiften på fem ställen med fem olika livslängder och fem
olika behörighetsmodeller. Källan raderas, kopian ligger kvar.

---

## 2. Ägarskap — den enda spärren som håller

`ai.okf.concept` är den plats där data från många modeller möts. Det är
därför det är där ett integritetsfel gör mest skada: en läckt koncept-rad är
inte en läckt rad, den är en läckt rad **som är designad att hittas**.

Varje koncept har exakt en ägare:

| `owner_*` | Betyder | Vem ser den |
|---|---|---|
| `owner_company_id` | Företagsminne | Alla i företaget |
| `owner_user_id` | Personligt minne | Bara den användaren |
| `owner_coworker_id` | En coworkers minne | Coworkerns `chat_user_id` |

**`scope=None` får aldrig skickas.** Det var den gamla `Hybridsök (AI)`-läckan:
en sökning utan ägare returnerar allas koncept. Sökvägen går genom
`visible_to()`, som resolverar användarens ägarpar — företag, personligt, och
varje coworker användaren äger — och anropar `_okf_search` **en gång per
par**. Aldrig en union, aldrig ett tomt scope.

**En läsbar koncept-rad betyder inte en läsbar källpost.** Ett koncept kan
peka på en `dms.file` användaren inte får öppna. `_okf_source_action()`
kontrollerar därför både modellrättighet och postregel innan den bygger en
länk; en otillgänglig källa renderas **utan** länk, inte med en trasig.

---

## 3. Textutvinning — när innehåll blir sökbart

Ett dokument vars text utvinns går från "en fil någon kan öppna" till "en
text alla kan söka i". Det är hela poängen med utvinning, och det är där
integritetsrisken sitter.

**Den generiska OKF-källan vägrar indexera fält med `groups`.** Det skyddar
`hr.employee.ssnid`, `private_email`, `private_phone` och liknande — de
kopieras aldrig till ett koncept.

**Utvunnen text har ingen sådan spärr.** Den är inte ett fält med `groups`;
den är en sträng som kommer från en binärfil. En PDF med löner, ett foto av
ett sjukintyg, en skannad legitimation — texten blir sökbar för alla som ser
konceptet, oavsett vilka regler som gäller för filen.

**Regeln:** en katalog vars innehåll är begränsat **måste** sätta
`extract_enabled = False`. Det finns ingen automatisk skyddsnät. Att sätta
det är ett beslut någon fattat; att inte sätta det är ett beslut någon
fattat genom att inte fatta det.

Samma sak gäller krypterade kataloger: chiffertext ska inte utvinnas, och
undantaget är en **egenskap på katalogen**, inte en heuristik på byten. Att
försöka dekryptera och gissa skulle tyst indexera en trasig PDF som om den
vore krypterad.

---

## 4. EXIF — bildens osynliga innehåll

Ett foto bär mer än bilden. EXIF-blocket innehåller upp till hundra fält, och
fyra av dem är personuppgifter i lagens mening:

| Fält | Vad det avslöjar | Känslighet |
|---|---|---|
| `GPSLatitude` / `GPSLongitude` | Var bilden togs — ofta ett hem | **Hög** — kan peka ut en persons bostad |
| `DateTimeOriginal` | När — arbetsvanor, sjukdagar, semester | Medel |
| `Make` / `Model` | Vilken enhet — kan knyta bilden till en person | Låg |
| `ImageDescription` / `UserComment` | Fritext någon skrev | Varierar — kan vara allt |

### 4.1 GPS är en personuppgift, inte metadata

En bild tagen hemma hos en anställd bär den anställdas hemadress. Att
indexera den i ett företagsminne är att publicera den.

**GPS-koordinater får aldrig hamna i `okf_body`.** `"57.7089 11.9746"` blir
två sökbara tal som matchar allt och inget — och gör adressen sökbar för alla
som ser konceptet. GPS hör i ett numeriskt fält, där det kan filtreras på
och skyddas av samma regler som resten av posten.

**Spärren är per katalog, som för text.** En katalog med `extract_enabled =
False` utvinner ingen EXIF alls. Det finns inget separat GPS-reglage: en
katalog som inte får indexeras får inte heller berikas.

### 4.2 Vad som får bli tagg, vad som måste bli fält

En tagg är **en klassificering någon kan välja**. EXIF är **mätvärden en
kamera skrev**. Att göra ett mätvärde till en tagg är att skapa en tagg per
värde — och en kamera skriver ett nytt värde varje sekund.

| Data | Lagring | Varför |
|---|---|---|
| `DateTimeOriginal` | Fält | Kontinuerligt — som tagg blir det en tagg per sekund |
| `GPSLatitude` / `GPSLongitude` | Fält | Numeriskt — "inom 500 m" går inte att fråga med en tagg |
| `Make` / `Model` | Fält **och** tagg | Ändlig mängd (en handfull enheter), men ingen sätter den manuellt |
| `Orientation` | Fält | Renderingshjälp, inte en klassificering |
| `ImageDescription` / `UserComment` | Text → `okf_body` | Fritext är sökbart innehåll |
| Övriga ~90 fält | `exif_raw` (Json) | Indexera det ni frågar på, dumpa resten |

**Räknestycket som avgör:** 100 000 foton × 3 taggar = 300 000 rader i
`dms_file_tag_rel` — hanterbart. Samma foton med `DateTimeOriginal` och GPS
som taggar = 200 000 extra taggar, varav de flesta med exakt ett
användningstillfälle. Det är inte en klassificering; det är en logg som råkar
bo i en many2many-tabell.

### 4.3 EXIF-text går samma väg som dokumenttext

`ImageDescription`, `UserComment` och `XPKeywords` är fritext. De läggs i
`okf_body` genom samma `_okf_body_source()` som dokumenttexten — ingen ny
väg, ingen ny sökning, ingen ny läckyta. Det som gäller för utvunnen text
gäller för EXIF-text.

---

## 5. Vad som lämnar systemet

`ai_agent_core` skickar data till en LLM-provider. Det är en
**personuppgiftsbiträdesrelation** (Art. 28) och kräver avtal — men avtalet
är inte det tekniska problemet. Det tekniska problemet är **vad** som skickas.

| Flöde | Vad skickas | Kontroll |
|---|---|---|
| Chatt | Användarens meddelanden + verktygsresultat | Användarens egen inmatning |
| Verktygsanrop | Modellnamn, fältnamn, **postdata** | `odoo_read` skickar hela posten |
| Sammanfattning | `okf_body` trunkerad till `_okf_summary_max_chars` | Kroppens innehåll |
| Embedding | Sammanfattningen | Samma som ovan |

**`odoo_read` är den bredaste dörren.** Ett verktygsanrop kan skicka en hel
`hr.employee`-post till providern, inklusive fält användaren själv inte ser i
formuläret. Verktyget respekterar Odoos åtkomstregler — men en användare med
läsrätt på en post får hela posten, inte bara de fält som är synliga i vyn.

**Det finns ingen automatiskt maskering, och det är ett medvetet val.** Att
gissa vilka fält som är känsliga kräver en lista som blir fel — och en
felaktig maskering är värre än ingen, för den ser ut som skydd. I stället
gäller: **den som ger en användare läsrätt på en post har gett dem posten.**

---

## 6. Gallring — kopian är problemet

En personuppgift som raderas i sin källa ska raderas överallt. I praktiken är
det svåraste inte raderingen utan **alla ställen den tog vägen**:

```
   dms.file (källan)
       │
       ├──▶ _extracted_text        (kopia 1 — utvunnen text)
       │        │
       │        └──▶ okf_body      (kopia 2 — konceptets kropp)
       │                 │
       │                 ├──▶ summary       (kopia 3 — LLM-sammanfattning)
       │                 └──▶ embedding     (kopia 4 — vektor, ej reversibel)
       │
       └──▶ exif_raw               (kopia 5 — bildens metadata)
```

**Vektorn är den svåraste.** En embedding är inte krypterad och inte
reversibel i strikt mening — men den är inte heller anonym, och `embedding <=>
query` kan hitta tillbaka till innehållet. Att radera raden räcker: vektorn
bor i raden.

**Regeln:** konceptet speglar källan (`okf_contract.stale_policy = "source"`).
När källan försvinner ska konceptet försvinna — inte bli `superseded` och
ligga kvar i sökindexet. Det var precis vad som hände innan `_okf_upsert`
blev en uppdatering i stället för en logg: varje ändring blev en ny rad, den
gamla blev `superseded`, och **båda låg kvar i sökindexet**.

---

## 7. Vad den här filen inte täcker

- **Juridik.** Den beskriver systemets beteende, inte vad som är lagligt i en
  given situation. Rättslig grund, DPIA och biträdesavtal avgörs per
  installation.
- **Personuppgiftsincidenter.** Anmälan inom 72 timmar (Art. 33) hanteras av
  incidentprocessen, inte här. `failures.md` säger vad som händer när något
  går fel; den här filen säger vad som får hända i normal drift.
- **Den registrerades rättigheter.** Registerutdrag, rättelse och radering
  kräver en ingång i systemet — den finns inte i `ai_agent_core` idag.

---

## 8. Checklista för en ny modul som matar OKF

Innan en modul registrerar sig med `_okf_register_indexable()`:

1. **Vilka fält blir kroppen?** Läs `_okf_body_source()`. Den plockar upp
   alla `html`- och `text`-fält utan `groups`. Är något av dem känsligt?
2. **Finns det en binärfil?** Om text utvinns ur den: vem äger innehållet,
   och kan katalogen stängas av?
3. **Finns det metadata med plats eller tid?** EXIF, IP-adresser,
   GPS — det ska i ett fält, inte i kroppen.
4. **Vad är ägarskapet?** Ett koncept utan ägare syns för alla. Sätt
   `owner_company_id`, `owner_user_id` eller `owner_coworker_id` — aldrig
   inget.
5. **Vad händer när källan raderas?** Följer konceptet med, eller blir det
   kvar som en spegel av något som inte finns?
