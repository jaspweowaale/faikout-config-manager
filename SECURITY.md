# Security

This tool can read and (optionally) write configuration settings on real air
conditioning hardware over MQTT. Please read this before deploying it,
especially before setting `ENABLE_LIVE_WRITES=true`.

## Threat model summary

- The web UI is the only network-facing component. It requires HTTP Basic
  Auth on **every** route (enforced by a single `before_request` hook, not
  per-route, so a new route added later can't accidentally ship unauthenticated).
- Credentials are generated randomly on first run and stored, owner-readable
  only (`chmod 600`), under your data directory (`auth.json`). They are never
  logged and never returned by any API response.
- MQTT broker credentials are stored the same way (`mqtt_config.json`,
  `chmod 600`). The broker password is never echoed back by `GET /api/mqtt-settings`.
- **Deploy this behind HTTPS** if it's reachable outside a trusted LAN (e.g. a
  reverse proxy with a real certificate, or a VPN/Tailscale-style overlay).
  Basic Auth over plain HTTP sends credentials in the clear on the wire.
- Sensitive setting values (`mqtt.user`, `mqtt.host`, `wifi.ssid`, `wifi.ip`,
  `wifi.gw`, `wifi.dns`) are redacted (`***redacted***`) in any unrestricted
  display or log line -- see `faikout_config/safety.py`.

## Live writes are off by default

Setting `ENABLE_LIVE_WRITES=true` (environment variable only -- there is no UI
toggle, by design) is the single switch that allows sync/restore/profile-apply
to actually write to your controllers. Until you set it:

- Reading, comparing, and backing up live settings all work normally.
- Any write attempt returns a clear 403 explaining why.

Only enable it once you've reviewed what it allows and you're comfortable with
this tool writing to hardware you own.

## Firmware-update protection

No code path in this project -- sync, restore, or profile-apply -- is allowed
to set `ota.auto` to a truthy value, in any form (Python `bool`/`int`/`float`,
or a string like `"true"`/`"1"`/`"yes"` -- both are checked, since values
coming from the web UI's HTML inputs always arrive as strings first). This is
enforced at the lowest shared layer (`faikout_config/safety.py`,
`assert_does_not_enable_ota_auto`) and is not a feature you can opt into or
configure around from within this tool. If you genuinely want to re-enable
automatic firmware updates on a controller, do it directly against the
device, outside this project.

## Reporting a vulnerability

Please open a private security advisory on this repository (GitHub's
"Report a vulnerability" button under the Security tab) rather than a public
issue, so there's time to fix it before it's public. If that's not available
to you, open an issue that says only "possible security issue, please contact
me" with no details, and a maintainer will follow up.

## Dependencies

This project has exactly two **direct** runtime dependencies: `flask` and
`paho-mqtt`. `requirements.txt` pins the exact version of both plus their
full transitive dependency closure (not just version ranges), so a fresh
install is reproducible. Keep both direct dependencies reasonably current;
`paho-mqtt` in particular changed its callback API between v1.x and v2.x,
and this project requires v2.x (`CallbackAPIVersion.VERSION2`).
