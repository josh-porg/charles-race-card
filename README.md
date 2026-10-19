Data branch for the lake conditions pages (https://josh-porg.github.io/charles-race-card/whatcom/ and /sammamish/):
`<lake>/live.json` (the lakeside CWOP weather stations' last few hours, refreshed every 15 minutes by
`.github/workflows/lakes-relay.yml` on main, from findu.com) and `<lake>/history/` (every reading since late August 2026,
for recalibrating the lake wind models). Public weather-station data; stations named by place.
