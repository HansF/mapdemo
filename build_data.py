"""Haal de brondata eenmalig op en schrijf compacte JSON naar data/.

Bronnen:
  * Gemeentegrenzen Vlaanderen: Digitaal Vlaanderen VRBG (WFS, geo.api.vlaanderen.be)
  * Gemeentegrenzen Wallonie + stations + spoorlijnen: OpenStreetMap via Overpass API

Gebruik:  python build_data.py
"""
from __future__ import annotations

import json
import pathlib
import time
import urllib.parse
import urllib.request

OUT = pathlib.Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
UA = {"User-Agent": "kaartje-demo/1.0 (static data build)"}

# Regio Vlaamse Ardennen + ruime omgeving
FLEMISH = [
    "Brakel", "Horebeke", "Kluisbergen", "Kruisem", "Lierde", "Maarkedal", "Oudenaarde",
    "Ronse", "Zottegem", "Zwalm", "Wortegem-Petegem", "Geraardsbergen", "Herzele",
    "Sint-Lievens-Houtem", "Pajottegem", "Gavere",
]
WALLOON = ["Flobecq", "Ellezelles", "Frasnes-lez-Anvaing", "Mont-de-l'Enclus", "Lessines", "Ath"]
BBOX = "50.55,3.2,51.2,4.3"  # S,W,N,E
GENT_ID = "008892007"  # Gent-Sint-Pieters (NMBS/iRail)


def get(url: str, data: bytes | None = None, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def overpass(query: str, tries: int = 8) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(tries):
        url = OVERPASS[attempt % len(OVERPASS)]
        try:
            print(f"    overpass poging {attempt + 1} -> {url.split('/')[2]}")
            data = json.loads(get(url, body))
            if not data.get("elements"):
                raise RuntimeError("leeg antwoord: " + str(data.get("remark", ""))[:120])
            return data
        except Exception as exc:  # noqa: BLE001 - alle netwerkfouten zijn herbruikbaar
            last = exc
            print(f"    mislukt: {exc}")
            time.sleep(5)
    raise RuntimeError(f"Overpass faalde na {tries} pogingen: {last}")


def simplify(points, tol):
    """Douglas-Peucker op [x, y]-paren; tol in graden."""
    if len(points) < 3:
        return points
    stack, keep = [(0, len(points) - 1)], {0, len(points) - 1}
    while stack:
        lo, hi = stack.pop()
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
        best, bi = 0.0, -1
        for i in range(lo + 1, hi):
            px, py = points[i]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best:
                best, bi = d, i
        if best > tol and bi > 0:
            keep.add(bi)
            stack += [(lo, bi), (bi, hi)]
    return [points[i] for i in sorted(keep)]


TOL = 0.00025  # ~25 m vereenvoudiging: ruim genoeg voor een regiokaart


def round_ring(ring, nd=5):
    """Vereenvoudig, rond af en verwijder opeenvolgende duplicaten (kleinere bestanden)."""
    pts = [[p[0], p[1]] for p in ring]
    if len(pts) > 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) > 4:
        # Een gesloten ring heeft een nul-lange basislijn; splits hem daarom in twee
        # halve ringen via het punt dat het verst van het startpunt ligt.
        ax, ay = pts[0]
        far = max(range(len(pts)), key=lambda i: (pts[i][0] - ax) ** 2 + (pts[i][1] - ay) ** 2)
        pts = simplify(pts[:far + 1], TOL)[:-1] + simplify(pts[far:] + [pts[0]], TOL)[:-1]
    out = []
    for lon, lat in pts:
        pt = [round(lon, nd), round(lat, nd)]
        if not out or out[-1] != pt:
            out.append(pt)
    if len(out) > 3 and out[0] != out[-1]:
        out.append(out[0])
    return out


def shrink_geometry(geom):
    if geom["type"] == "Polygon":
        geom["coordinates"] = [round_ring(r) for r in geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        geom["coordinates"] = [[round_ring(r) for r in poly] for poly in geom["coordinates"]]
    return geom


def flemish_boundaries() -> list[dict]:
    names = ",".join(f"'{n}'" for n in FLEMISH)
    url = (
        "https://geo.api.vlaanderen.be/VRBG/wfs?service=WFS&version=2.0.0&request=GetFeature"
        "&typeNames=VRBG:Refgem&outputFormat=application/json&srsName=EPSG:4326"
        "&CQL_FILTER=" + urllib.parse.quote(f"NAAM IN ({names})")
    )
    fc = json.loads(get(url))
    feats = []
    for f in fc["features"]:
        if not f.get("geometry"):
            continue
        feats.append({
            "type": "Feature",
            "properties": {"name": f["properties"]["NAAM"], "src": "VRBG"},
            "geometry": shrink_geometry(f["geometry"]),
        })
    print(f"  Vlaamse gemeenten: {len(feats)} ({', '.join(sorted(f['properties']['name'] for f in feats))})")
    return feats


def rel_to_polygon(rel, nodes_by_way):
    """Zet een OSM-relatie met 'out geom' om naar een (Multi)Polygon."""
    segs = [list(m["geometry"]) for m in rel.get("members", [])
            if m.get("role") in ("outer", "") and m.get("geometry")]
    rings, cur = [], []
    while segs:
        if not cur:
            cur = segs.pop(0)
            continue
        end = cur[-1]
        for i, s in enumerate(segs):
            if abs(s[0]["lat"] - end["lat"]) < 1e-7 and abs(s[0]["lon"] - end["lon"]) < 1e-7:
                cur += segs.pop(i)[1:]
                break
            if abs(s[-1]["lat"] - end["lat"]) < 1e-7 and abs(s[-1]["lon"] - end["lon"]) < 1e-7:
                cur += list(reversed(segs.pop(i)))[1:]
                break
        else:
            rings.append(cur)
            cur = []
    if cur:
        rings.append(cur)
    polys = [[round_ring([[p["lon"], p["lat"]] for p in r])] for r in rings if len(r) > 3]
    if not polys:
        return None
    return {"type": "MultiPolygon", "coordinates": polys}


def walloon_boundaries() -> list[dict]:
    names = "|".join(n.replace("'", ".") for n in WALLOON)
    q = (f'[out:json][timeout:180];relation["boundary"="administrative"]["admin_level"="8"]'
         f'["name"~"^({names})$"](50.5,3.3,50.95,4.3);out geom;')
    data = overpass(q)
    feats = []
    for rel in data["elements"]:
        geom = rel_to_polygon(rel, None)
        if geom:
            feats.append({
                "type": "Feature",
                "properties": {"name": rel["tags"].get("name"), "src": "OSM"},
                "geometry": geom,
            })
    print(f"  Waalse gemeenten: {len(feats)} ({', '.join(sorted(f['properties']['name'] for f in feats))})")
    return feats


def osm_stations() -> dict[str, dict]:
    """OSM-stations in de bbox, voor extra context (halte vs. station)."""
    q = f'[out:json][timeout:180];node["railway"~"^(station|halt)$"]["name"]({BBOX});out body;'
    data = overpass(q)
    out = {}
    for e in data["elements"]:
        t = e.get("tags", {})
        name = t.get("name")
        if not name or t.get("disused") or t.get("station") in ("subway", "light_rail", "tram"):
            continue
        out[norm_name(name)] = {
            "osm": e["id"],
            "halt": t.get("railway") == "halt",
            "lat": round(e["lat"], 5),
            "lon": round(e["lon"], 5),
        }
    print(f"  OSM-stations in bbox: {len(out)}")
    return out


def norm_name(n: str) -> str:
    n = n.lower().split("/")[0].strip()
    for a, b in [("ë", "e"), ("é", "e"), ("è", "e"), ("ï", "i"), ("ô", "o"), ("-", " ")]:
        n = n.replace(a, b)
    return " ".join(n.split())


def irail(path: str, params: dict, tries: int = 4):
    params = {**params, "format": "json", "lang": "nl"}
    url = f"https://api.irail.be/{path}/?" + urllib.parse.urlencode(params)
    last = None
    for _ in range(tries):
        try:
            return json.loads(get(url, timeout=60))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(3)
    raise RuntimeError(f"iRail {path} faalde: {last}")


def irail_stations() -> list[dict]:
    """Alle NMBS-stations; filter op de regio-bbox."""
    s, w, n, e = (float(x) for x in BBOX.split(","))
    out = []
    for st in irail("stations", {})["station"]:
        lat, lon = float(st["locationY"]), float(st["locationX"])
        if s <= lat <= n and w <= lon <= e:
            out.append({
                "id": st["id"].split(".")[-1],
                "name": st["standardname"],
                "label": st["name"],
                "lat": round(lat, 5),
                "lon": round(lon, 5),
            })
    print(f"  iRail-stations in bbox: {len(out)}")
    return out


def travel_times(sts: list[dict], osm: dict[str, dict]) -> list[dict]:
    """Echte reistijd naar Gent-Sint-Pieters via iRail.

    We bemonsteren meerdere momenten van de dag en houden de snelste verbinding aan
    (dat is wat 'indicatieve reistijd' op zo'n kaart betekent), plus de mediaan als
    maat voor een doorsnee rit.
    """
    # volgende dinsdag = representatieve werkdag
    t = time.localtime()
    days = (1 - t.tm_wday) % 7 or 7
    day = time.strftime("%d%m%y", time.localtime(time.time() + days * 86400))
    windows = ("0700", "0830", "1200", "1700")
    print(f"  Reistijden voor {day} (ddmmjj), vertrek rond {', '.join(windows)}")

    for i, st in enumerate(sts, 1):
        meta = osm.get(norm_name(st["name"])) or osm.get(norm_name(st["label"])) or {}
        st["halt"] = meta.get("halt", False)
        if st["id"] == GENT_ID:
            st.update(train=0, typical=0, transfers=0, direct=True, runs=0)
            continue
        runs = []
        for hhmm in windows:
            try:
                data = irail("connections", {
                    "from": st["id"], "to": GENT_ID, "date": day, "time": hhmm,
                    "timesel": "depart", "results": 4,
                })
            except Exception as exc:  # noqa: BLE001
                print(f"    {st['name']}: {exc}")
                continue
            for c in data.get("connection", []):
                vias = c.get("vias")
                runs.append((int(c["duration"]) // 60, int(vias["number"]) if vias else 0))
            time.sleep(0.35)  # vriendelijk voor de gratis API
        if runs:
            runs.sort()
            direct_runs = [r for r in runs if r[1] == 0]
            best = (direct_runs or runs)[0]
            st["train"] = best[0]
            st["transfers"] = best[1]
            st["direct"] = best[1] == 0
            st["typical"] = runs[len(runs) // 2][0]
            st["runs"] = len(runs)
        else:
            st["train"] = None
        print(f"    {i:3}/{len(sts)} {st['name']:<30} "
              + (f"{st['train']:>3} min ({st['transfers']} overstap), doorsnee {st['typical']} min"
                 if st.get("train") is not None else "geen verbinding"))

    ok = [s for s in sts if s.get("train") is not None]
    print(f"  Reistijden gevonden voor {len(ok)}/{len(sts)} stations")
    return ok


def rail() -> list[list[list[float]]]:
    q = f'[out:json][timeout:180];way["railway"="rail"][!"service"]({BBOX});out geom qt;'
    data = overpass(q)
    lines = []
    for w in data["elements"]:
        g = w.get("geometry")
        if not g or len(g) < 2:
            continue
        pts, prev = [], None
        for p in g:
            pt = [round(p["lat"], 5), round(p["lon"], 5)]
            if pt != prev:
                pts.append(pt)
                prev = pt
        pts = simplify(pts, TOL)
        if len(pts) > 1:
            lines.append(pts)
    print(f"  Spoorsegmenten: {len(lines)}")
    return lines


def dissolve(feats: list[dict]) -> dict | None:
    """Smelt de gemeenten samen tot een buitenomtrek van de regio."""
    try:
        from shapely.geometry import shape, mapping
        from shapely.ops import unary_union
    except ImportError:
        print("  shapely ontbreekt: buitenomtrek overgeslagen (pip install shapely)")
        return None
    geoms = [shape(f["geometry"]).buffer(0) for f in feats]
    # kleine buffer dicht de haarscheurtjes tussen de Vlaamse en Waalse bronbestanden
    merged = unary_union([g.buffer(1e-5) for g in geoms]).buffer(-1e-5).simplify(TOL)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Vlaamse Ardennen en omgeving"},
         "geometry": mapping(merged)}]}


def write(name: str, obj) -> None:
    path = OUT / name
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  -> {path.relative_to(OUT.parent)}  ({path.stat().st_size / 1024:.0f} kB)")


def main() -> None:
    print("1/4 Gemeentegrenzen")
    feats = flemish_boundaries()
    try:
        feats += walloon_boundaries()
    except Exception as exc:  # noqa: BLE001
        print(f"  WAARSCHUWING Waalse grenzen overgeslagen: {exc}")
    write("region.geojson", {"type": "FeatureCollection", "features": feats})
    outline = dissolve(feats)
    if outline:
        write("outline.geojson", outline)

    print("2/4 Stations (iRail/NMBS + OSM)")
    try:
        osm = osm_stations()
    except Exception as exc:  # noqa: BLE001
        print(f"  WAARSCHUWING OSM-stations overgeslagen: {exc}")
        osm = {}

    print("3/4 Echte reistijden naar Gent (iRail)")
    sts = travel_times(irail_stations(), osm)
    sts.sort(key=lambda s: s["train"])
    write("stations.json", sts)

    print("4/4 Spoorlijnen (OSM)")
    try:
        write("rail.json", rail())
    except Exception as exc:  # noqa: BLE001
        print(f"  WAARSCHUWING spoorlijnen overgeslagen: {exc}")

    meta = {
        "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "bbox": BBOX,
        "gent": GENT_ID,
        "stations": len(sts),
        "sources": [
            "Gemeentegrenzen Vlaanderen: Digitaal Vlaanderen - VRBG (geo.api.vlaanderen.be), via Datavindplaats",
            "Gemeentegrenzen Wallonie, stations en spoorlijnen: (c) OpenStreetMap-bijdragers (ODbL), via Overpass API",
            "Stations en reistijden: iRail API (api.irail.be), op basis van NMBS/SNCB-gegevens",
        ],
    }
    write("meta.json", meta)
    print("Klaar.")


if __name__ == "__main__":
    main()
