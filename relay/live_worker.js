// Lake conditions pages (Whatcom, Sammamish): the lakeside weather stations read live, when a page opens.
//
// A Cloudflare Worker (free plan). GET ?lake=whatcom returns the same JSON as the relay's <lake>/live.json
// (relay/lake_relay.py), but read from findu.com (CWOP) and King County's buoy feed at that moment, so the
// readings are minutes old. GitHub runs the relay's 15-minute schedule only a few times a day, so its live.json
// is usually hours old; the relay still keeps the history and remains the page's fallback.
//
// It reads only the stations listed in relay/lakes.json on the main branch (it is not an open proxy), and keeps
// each lake's answer for two minutes, so findu sees at most one small request per station per two minutes
// however many people open the page. Each station's recent readings are merged with the relay's: the buoy reports
// hourly, so its last few hours come from the relay, and a station findu cannot reach still shows what the relay has.
//
// Deploy: Cloudflare dashboard > Workers & Pages > Create > Worker; paste this file; Deploy. Put the Worker's URL
// in each lake.json as "live" (the page adds ?lake=<lake>).
// Stop: delete "live" from the lake.json files (the page falls back to the relay), or delete the Worker in the
// Cloudflare dashboard. Nothing here runs on a schedule; it only runs when a page asks.

const REPO_RAW = "https://raw.githubusercontent.com/josh-porg/charles-race-card";
const UA = {"User-Agent": "Lake rowing conditions page (github.com/josh-porg/charles-race-card); a few stations, read when the page opens"};
const KEEP_MS = 120e3, WINDOW_MS = 6 * 3600e3;
const memo = new Map();                                  // per isolate: url or lake -> {at, value}

async function cached(key, ttl, make){
  const m = memo.get(key);
  if (m && Date.now() - m.at < ttl) return m.value;
  const value = await make();
  memo.set(key, {at: Date.now(), value});
  return value;
}

async function text(url, timeoutMs){
  const r = await fetch(url, {headers: UA, signal: AbortSignal.timeout(timeoutMs || 8000)});
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.text();
}

// findu's weather table: time (UTC, yyyymmddhhmmss), temperature (F), direction, speed (mph), gust (mph), ...
function parseFindu(html){
  const out = [];
  for (const line of html.replace(/<[^>]+>/g, " ").split("\n")){
    const f = line.trim().split(/\s+/);
    if (f.length < 5 || !/^\d{14}$/.test(f[0])) continue;
    const t = Date.UTC(+f[0].slice(0, 4), +f[0].slice(4, 6) - 1, +f[0].slice(6, 8), +f[0].slice(8, 10), +f[0].slice(10, 12), +f[0].slice(12, 14));
    const mph = parseFloat(f[3]), dir = parseFloat(f[2]), gust = parseFloat(f[4]);
    if (isFinite(mph) && isFinite(dir)) out.push([new Date(t).toISOString(), mph, dir, isFinite(gust) ? gust : null]);
  }
  return out;
}

// King County's buoy map feed: records split by ^, fields by |. Times are Pacific daylight time all year
// (as lake_relay.py reads them), wind in m/s, direction as a compass point; water temperature near the surface.
const COMPASS = Object.fromEntries("N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split(" ").map((n, i) => [n, 22.5 * i]));
function kcTime(s){
  const m = /^(\d+)\/(\d+)\/(\d{4}) (\d+):(\d+):(\d+) ([AP]M)$/.exec(s);
  if (!m) return null;
  const h = (+m[4] % 12) + (m[7] === "PM" ? 12 : 0);
  return new Date(Date.UTC(+m[3], +m[1] - 1, +m[2], h + 7, +m[5], +m[6])).toISOString();
}
function parseKingCounty(txt){
  const res = {obs: []};
  for (const rec of txt.split("^")){
    const f = rec.trim().replace(/^\|/, "").split("|").map(x => x.trim());
    if (f.length < 10 || f[0] !== "Sammamish") continue;
    const tw = f[5] && f[6] ? kcTime(f[6]) : null;
    if (tw) Object.assign(res, {water_c: parseFloat(f[5]), water_t: tw});
    const t = f[1] ? kcTime(f[1]) : null, d = COMPASS[f[4].replace("from", "").trim().toUpperCase()];
    if (t && d != null && f[3]) res.obs.push([t, parseFloat(f[3]) / 0.44704, d, null]);
  }
  return res;
}

function merge(a, b){                                    // union of [time, mph, dir, gust] by time, oldest first, last 6 h
  const by = new Map();
  for (const o of [...(a || []), ...(b || [])]) by.set(Date.parse(o[0]), o);
  const cut = Date.now() - WINDOW_MS;
  return [...by.entries()].filter(([t]) => t > cut).sort((x, y) => x[0] - y[0]).map(([, o]) => o);
}

async function lakeLive(lake){
  const lakes = await cached("lakes.json", 3600e3, async () => JSON.parse(await text(REPO_RAW + "/main/relay/lakes.json")));
  const stations = lakes[lake];
  if (!stations) return null;
  let relay = null;
  try { relay = JSON.parse(await text(REPO_RAW + "/lakes-data/" + lake + "/live.json?t=" + Math.floor(Date.now() / 300e3))); } catch (e) {}
  const live = {updated: new Date().toISOString(), source: "findu.com (CWOP), read live", live: true,
                relay_updated: relay ? relay.updated : null, stations: {}};
  await Promise.all(Object.entries(stations).map(async ([call, meta]) => {
    const old = relay && relay.stations && relay.stations[call] || {};
    try {
      if (meta.source === "kingcounty"){
        const k = parseKingCounty(await text("https://green2.kingcounty.gov/lake-buoy/GenerateMapData.aspx"));
        const s = Object.assign({}, meta, {obs: merge(old.obs, k.obs)});
        if (k.water_c != null) Object.assign(s, {water_c: k.water_c, water_t: k.water_t});
        else if (old.water_c != null) Object.assign(s, {water_c: old.water_c, water_t: old.water_t});
        live.stations[call] = s;
      } else {
        const obs = parseFindu(await text("http://www.findu.com/cgi-bin/wx.cgi?call=" + encodeURIComponent(call) + "&last=3&units=english"));
        live.stations[call] = Object.assign({}, meta, {obs: merge(old.obs, obs)});
      }
    } catch (e) {                                        // one station failing must not stop the others
      live.stations[call] = Object.assign({}, meta, {obs: merge(old.obs, []), error: String(e.name || e)},
        old.water_c != null ? {water_c: old.water_c, water_t: old.water_t} : {});
    }
  }));
  return live;
}

export default {
  async fetch(request){
    const cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET", "Content-Type": "application/json"};
    if (request.method === "OPTIONS") return new Response(null, {headers: cors});
    const lake = new URL(request.url).searchParams.get("lake") || "";
    if (!/^[a-z]{1,30}$/.test(lake)) return new Response(JSON.stringify({error: "?lake= one of the lakes in relay/lakes.json"}), {status: 400, headers: cors});
    try {
      const live = await cached("lake:" + lake, KEEP_MS, () => lakeLive(lake));
      if (!live) { memo.delete("lake:" + lake); return new Response(JSON.stringify({error: "unknown lake"}), {status: 404, headers: cors}); }
      return new Response(JSON.stringify(live), {headers: Object.assign({"Cache-Control": "public, max-age=60"}, cors)});
    } catch (e) {
      memo.delete("lake:" + lake);
      return new Response(JSON.stringify({error: String(e.message || e)}), {status: 502, headers: cors});
    }
  }
};
