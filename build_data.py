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
    pts = simplify([[p[0], p[1]] for p in ring], TOL)
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


def stations() -> list[dict]:
    q = f'[out:json][timeout:180];node["railway"~"^(station|halt)$"]["name"]({BBOX});out body;'
    data = overpass(q)
    seen, out = set(), []
    for e in data["elements"]:
        t = e.get("tags", {})
        name = t.get("name")
        if not name or name in seen:
            continue
        if t.get("disused") or t.get("abandoned") or "disused:railway" in t:
            continue
        if t.get("station") in ("subway", "light_rail", "tram"):
            continue
        seen.add(name)
        out.append({
            "name": name,
            "lat": round(e["lat"], 5),
            "lon": round(e["lon"], 5),
            "halt": t.get("railway") == "halt",
            "osm": e["id"],
        })
    out.sort(key=lambda s: s["name"])
    print(f"  Stations: {len(out)}")
    return out


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


def write(name: str, obj) -> None:
    path = OUT / name
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  -> {path.relative_to(OUT.parent)}  ({path.stat().st_size / 1024:.0f} kB)")


def main() -> None:
    print("1/3 Gemeentegrenzen")
    feats = flemish_boundaries()
    try:
        feats += walloon_boundaries()
    except Exception as exc:  # noqa: BLE001
        print(f"  WAARSCHUWING Waalse grenzen overgeslagen: {exc}")
    write("region.geojson", {"type": "FeatureCollection", "features": feats})

    print("2/3 Stations (OSM)")
    write("stations.json", stations())

    print("3/3 Spoorlijnen (OSM)")
    write("rail.json", rail())

    meta = {
        "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "bbox": BBOX,
        "sources": [
            "Gemeentegrenzen Vlaanderen: Digitaal Vlaanderen - VRBG (geo.api.vlaanderen.be), via Datavindplaats",
            "Gemeentegrenzen Wallonie, stations en spoorlijnen: (c) OpenStreetMap-bijdragers (ODbL), via Overpass API",
        ],
    }
    write("meta.json", meta)
    print("Klaar.")


if __name__ == "__main__":
    main()
