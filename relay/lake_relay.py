"""Relay the lakeside weather stations (CWOP) to the lake conditions pages, every 15 minutes.

Run by .github/workflows/lakes-relay.yml; the stations per lake are in lakes.json beside this file. The stations report every 5-15 minutes into the Citizen Weather
Observer Program (APRS); findu.com keeps their recent reports, but not in a form a web page may read, and the
National Weather Service relays them about four hours late. This reads each station's last few hours from
findu (one small request per station, spaced out), writes <lake>/live.json for each page, and appends new
readings to <lake>/history/<call>.csv so the models can be recalibrated as the record grows
(data/local/lakes/calibrate.py).

Usage: python lake_relay.py OUTDIR   (OUTDIR is the checkout of the lakes-data branch)
Standard library only.
"""
import csv, json, os, re, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

LAKES = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lakes.json")))
UA = {"User-Agent": "Lake rowing conditions relay (github.com/josh-porg/charles-race-card); a few stations every 15 min"}
FIELDS = ["t", "temp_f", "dir", "mph", "gust_mph"]


COMPASS = {n: 22.5 * i for i, n in enumerate("N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split())}


def kingcounty(call):
    """King County's lake buoy: the latest hourly reading from the buoy map's feed (no browser access there).
    Times are Pacific daylight time all year (as the buoy history fits best), wind in m/s, direction as a point."""
    url = "https://green2.kingcounty.gov/lake-buoy/GenerateMapData.aspx"
    txt = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf-8", "replace")
    out = []
    for rec in txt.split("^"):
        f = [x.strip() for x in rec.strip().lstrip("|").split("|")]
        if len(f) >= 10 and f[0] == "Sammamish" and f[1]:
            t = datetime.strptime(f[1], "%m/%d/%Y %I:%M:%S %p") + timedelta(hours=7)
            d = COMPASS.get(f[4].replace("from", "").strip().upper())
            if d is None or not f[3]:
                continue
            out.append(dict(t=t.strftime("%Y%m%d%H%M%S"), temp_f="%.1f" % (float(f[2]) * 9 / 5 + 32) if f[2] else "",
                            dir="%.0f" % d, mph="%.1f" % (float(f[3]) / 0.44704), gust_mph=""))
    return out


def readings(call, hours=4):
    url = "http://www.findu.com/cgi-bin/wx.cgi?call=%s&last=%d&units=english" % (call, hours)
    html = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf-8", "replace")
    out = []
    for line in re.sub(r"<[^>]+>", " ", html).splitlines():
        f = line.split()
        if len(f) >= 5 and re.fullmatch(r"\d{14}", f[0]):
            out.append(dict(zip(FIELDS, f[:5])))
    return sorted(out, key=lambda r: r["t"])


def relay_lake(outdir, stations):
    os.makedirs(os.path.join(outdir, "history"), exist_ok=True)
    live = dict(updated=datetime.now(timezone.utc).isoformat(timespec="seconds"), source="findu.com (CWOP)", stations={})
    for call, meta in stations.items():
        try:
            rows = kingcounty(call) if meta.get("source") == "kingcounty" else readings(call)
        except Exception as e:                       # one station failing must not stop the others
            live["stations"][call] = dict(meta, error=type(e).__name__, obs=[])
            continue
        path = os.path.join(outdir, "history", call + ".csv")
        if meta.get("source") == "kingcounty" and os.path.exists(path):
            # one reading an hour: keep the last few hours from what this relay has stored
            seen = {r["t"] for r in rows}
            rows = [r for r in list(csv.DictReader(open(path)))[-8:] if r["t"] not in seen] + rows
        obs = []
        for r in rows:
            try:
                t = datetime.strptime(r["t"], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()
                obs.append([t, float(r["mph"]), float(r["dir"]), float(r["gust_mph"]) if r["gust_mph"] else None])
            except ValueError:
                pass
        obs = [o for o in obs if datetime.fromisoformat(o[0]) > datetime.now(timezone.utc) - timedelta(hours=6)]
        live["stations"][call] = dict(meta, obs=obs)
        have = set()
        if os.path.exists(path):
            have = {line.split(",", 1)[0] for line in open(path)}
        new = [r for r in rows if r["t"] not in have]
        if new:
            exists = os.path.exists(path)
            with open(path, "a", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS)
                if not exists:
                    w.writeheader()
                w.writerows(new)
        time.sleep(3)
    json.dump(live, open(os.path.join(outdir, "live.json"), "w"), separators=(",", ":"))
    return {c: (len(s["obs"]), s["obs"][-1][0] if s["obs"] else None) for c, s in live["stations"].items()}


def main(outdir):
    for lake, stations in LAKES.items():
        print(lake, relay_lake(os.path.join(outdir, lake), stations))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
