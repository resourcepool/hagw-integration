# HAGW integration for Home Assistant

The Home Assistant side of [HAGW](https://hagw.link), the Home Assistant
Gateway. It lets cloud services such as Google Assistant and Amazon Alexa reach
your Home Assistant **without exposing it to the internet**.

The integration opens one outgoing, encrypted connection (gRPC over TLS) to the
gateway. The gateway gives you a public HTTPS address, lets through only the
calls of the services you switched on, and the integration replays them on your
Home Assistant, locally. No port is opened on your router.

Full documentation: **[hagw.link/docs](https://hagw.link/docs)**.

## Install

### With HACS

1. In Home Assistant, open *HACS* → ⋮ → *Custom repositories*, add
   `https://github.com/resourcepool/hagw-integration` with the type
   *Integration*.
2. Search HACS for *Home Assistant Gateway* and choose *Download*.
3. Restart Home Assistant.

### Manually

Download `ha_gateway.zip` from the
[latest release](https://github.com/resourcepool/hagw-integration/releases/latest),
extract it into `<config>/custom_components/ha_gateway/` and restart Home
Assistant.

## Set up

1. **Create a token** in your HAGW panel, under *Home connection*. It is shown
   once.
2. **Add the integration**: *Settings → Devices & services → Add integration →
   Home Assistant Gateway*. It asks for:
   - **Gateway address**: your own address and port, for example
     `home.connect.hagw.link:443` (shown in your panel)
   - **Token**: the one from step 1

   and, with defaults that fit a standard installation, *Use TLS* (on), *Local
   Home Assistant URL* (`http://127.0.0.1:8123`) and *Forward the remote caller
   IP* (on). Before finishing it checks the address and the token with the
   gateway and says which one is wrong.
3. **Let Home Assistant see the real caller** instead of `127.0.0.1`: add to
   `configuration.yaml` and restart.

   ```yaml
   http:
     use_x_forwarded_for: true
     trusted_proxies:
       - 127.0.0.1
   ```

   This matters: without it every remote call appears to come from localhost,
   which defeats IP bans and, **if you use the `trusted_networks` auth provider
   with a range containing 127.0.0.1, would let remote callers log in without a
   password.** If you cannot change the `http` configuration, untick *Forward
   the remote caller IP* (Home Assistant answers `400` to forwarded requests it
   is not configured for) and make sure `trusted_networks` does not cover
   loopback.

Then switch on the services you want in your panel, and follow the guides for
[Google Assistant](https://hagw.link/docs/google-assistant) and
[Amazon Alexa](https://hagw.link/docs/alexa).

## Once connected

- A **Gateway connection** sensor (`binary_sensor`, connectivity) says whether
  the tunnel is up, for dashboards and alerts.
- The integration reconnects on its own (exponential backoff, up to 60 s).
  Home Assistant starts normally when the gateway is unreachable.
- If the token is replaced or revoked in the panel, Home Assistant shows a
  *re-authenticate* prompt asking for the new token, and refuses a token that
  belongs to a different account.
- *Reconfigure* changes the address or the other settings. *Download
  diagnostics* gives the settings without the token.
- One Home Assistant is connected per account; a new connection replaces the
  previous one.

## Development

| Command | What it does |
| --- | --- |
| `make zip` | Builds `dist/ha_gateway.zip`, the archive HACS installs |
| `make test` | Runs the setup flow inside a real Home Assistant, against a real gateway binary (`GATEWAY_BINARY`) |
| `make proto` | Regenerates `tunnel_pb2.py` from `proto/hagateway/v1/tunnel.proto` |

`make proto` and `make test` need [uv](https://docs.astral.sh/uv/).

### Releasing

HACS installs the `ha_gateway.zip` attached to the latest GitHub release
(`hacs.json` sets `zip_release`). To cut a release:

1. Set the new version in `custom_components/ha_gateway/manifest.json` **and**
   `const.py` (the build refuses if they differ).
2. Tag and push it: `git tag v0.2.0 && git push origin v0.2.0`.

`.github/workflows/release.yml` checks that the tag matches the version, builds
the archive and publishes the release. The *Integration* workflow runs hassfest
and the HACS validation, which a listing in the HACS default store requires.

## License

[MIT](LICENSE).
