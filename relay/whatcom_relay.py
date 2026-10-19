"""Relay the Lake Whatcom lakeside weather stations (CWOP) to the conditions page, every 15 minutes.

Run by .github/workflows/whatcom-relay.yml. The stations report every 5-15 minutes into the Citizen Weather
Observer Program (APRS); findu.com keeps their recent reports, but not in a form a web page may read, and the
National Weather Service relays them about four hours late. This reads each station's last few hours from
findu (one small request per station, spaced out), writes live.json for the page, and appends new readings to
history/<call>.csv so the model can be recalibrated as the record grows (data/local/whatcom/calibrate.py).

Usage: python whatcom_relay.py OUTDIR   (OUTDIR is the checkout of the whatcom-data branch)
Standard library only.
"""
import csv, json, os, re, sys, time, urllib.request
from datetime import datetime, timezone

STATIONS = {
    "DW0884": dict(name="Agate Bay, lake level", lat=48.76116, lon=-122.36255),
    "FW1160": dict(name="North shore, middle basin, lake level", lat=48.75083, lon=-122.3545),
    "FW6124": dict(name="Geneva shore", lat=48.745, lon=-122.3955),
    "EW7158": dict(name="Sudden Valley hillside, 90 m above the lake", lat=48.7035, lon=-122.32317),
}
UA = {"User-Agent": "Lake Whatcom rowing conditions relay (github.com/josh-porg/charles-race-card); 4 stations every 15 min"}
FIELDS = ["t", "temp_f", "dir", "mph", "gust_mph"]


def readings(call, hours=4):
    url = "http://www.findu.com/cgi-bin/wx.cgi?call=%s&last=%d&units=english" % (call, hours)
    html = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf-8", "replace")
    out = []
    for line in re.sub(r"<[^>]+>", " ", html).splitlines():
        f = line.split()
        if len(f) >= 5 and re.fullmatch(r"\d{14}", f[0]):
            out.append(dict(zip(FIELDS, f[:5])))
    return sorted(out, key=lambda r: r["t"])


def main(outdir):
    os.makedirs(os.path.join(outdir, "history"), exist_ok=True)
    live = dict(updated=datetime.now(timezone.utc).isoformat(timespec="seconds"), source="findu.com (CWOP)", stations={})
    for call, meta in STATIONS.items():
        try:
            rows = readings(call)
        except Exception as e:                       # one station failing must not stop the others
            live["stations"][call] = dict(meta, error=type(e).__name__, obs=[])
            continue
        obs = []
        for r in rows:
            try:
                t = datetime.strptime(r["t"], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()
                obs.append([t, float(r["mph"]), float(r["dir"]), float(r["gust_mph"])])
            except ValueError:
                pass
        live["stations"][call] = dict(meta, obs=obs)
        path = os.path.join(outdir, "history", call + ".csv")
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
    print("live.json:", {c: (len(s["obs"]), s["obs"][-1][0] if s["obs"] else None) for c, s in live["stations"].items()})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
