# Bereikbaarheid Gent met de trein — Vlaamse Ardennen

Een interactieve bereikbaarheidskaart die toont hoe vlot je vanuit de Vlaamse Ardennen
en omgeving met de trein in Gent raakt. Volledig opgebouwd uit open data.

**Live:** https://hansf.github.io/mapdemo/
**Hoe het gemaakt is:** [about.html](about.html) — colofon en methodiek voor GIS-collega's

## Wat je ziet

Een hexagonaal raster van ±4 400 cellen over 22 gemeenten. Elke cel is gekleurd
volgens de beste bereikbaarheid van Gent-Sint-Pieters vanuit die plek:

- **Reistijd tot Gent** (standaard) — tijd om je station te bereiken plus de echte treinrit
- **Afstand tot station** — hemelsbrede afstand tot de dichtstbijzijnde NMBS-halte

Interactief: wissel van modus, schuif de toegangssnelheid van te voet naar auto,
filter op rechtstreekse verbindingen, isoleer een legendeklasse door erop te klikken,
of beweeg over de kaart voor een detailweergave per locatie.

## Databronnen

| Bron | Gebruik | Licentie |
|---|---|---|
| VRBG (Digitaal Vlaanderen, WFS) | 16 Vlaamse gemeentegrenzen | Gratis Open Data Licentie Vlaanderen v1.2 |
| [OpenStreetMap](https://www.openstreetmap.org/) via Overpass API | 6 Waalse gemeentegrenzen + spoorlijnen | ODbL 1.0 |
| [iRail](https://docs.irail.be/) | 715 stations, reistijden en overstappen naar Gent | open, met `User-Agent` |
| Esri Light Gray Canvas | achtergrondkaart | geen API-sleutel nodig |

## Zelf draaien

```bash
# data opnieuw ophalen (duurt enkele minuten door iRail-rate limits)
pip install shapely
python build_data.py

# lokaal bekijken
python -m http.server 8000
# open http://localhost:8000/index.html
```

`build_data.py` schrijft naar `data/`:

| Bestand | Inhoud |
|---|---|
| `region.geojson` | 22 gemeenten, vereenvoudigd (±90 kB) |
| `outline.geojson` | gedissolveerde buitengrens van de regio |
| `stations.json` | 144 stations met coördinaten, reistijd en overstappen |
| `rail.json` | ±3 100 spoorsegmenten |
| `meta.json` | build-timestamp en bronvermelding |

Samen onder de 350 kB. De pagina haalt enkel deze statische bestanden op, dus ze
laadt meteen en werkt op GitHub Pages zonder server of API-sleutel.

## Aanpassen aan je eigen regio

In `build_data.py`:

- `FLEMISH` / `WALLOON` — de gemeentenamen
- `BBOX` — zoekvenster voor Overpass
- `GENT_ID` — het iRail-station waar alles naartoe gerekend wordt

In `index.html`:

- `MODES` — klassengrenzen en eenheden
- `HEXR` — resolutie van het hexagonraster
- `MAXLABELS` — aantal stations met een naamlabel

## Structuur

```
index.html        de kaart — Leaflet, alle logica client-side, geen build-stap
about.html        colofon en methodiek
build_data.py     de volledige datapipeline
data/             gegenereerde JSON (commit deze mee voor GitHub Pages)
```

## Belangrijkste aannames

- Toegang tot het station wordt geschat als hemelsbrede afstand × 1,32 (omwegfactor),
  gedeeld door de gekozen snelheid. Geen echte routering.
- Reistijden zijn bemonsterd over vier momenten van de dag (07:00 / 08:30 / 12:00 / 17:00),
  waarbij de snelste rechtstreekse verbinding voorrang krijgt.
- De cijfers wijken af van de originele posterkaart. Oudenaarde staat daar op 17 minuten;
  iRail geeft consequent 29 à 31. Wij tonen het controleerbare cijfer.

Zie [about.html](about.html) voor de volledige methodiek, inclusief de valkuilen die we
onderweg tegenkwamen.
