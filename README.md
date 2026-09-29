# HASDR — RTL-SDR for Home Assistant

HASDR brings one or more RTL-SDR receivers directly into Home Assistant for spectrum scanning, RF activity detection, and protocol decoding.

The normal setup is **local-first**. Users do not configure a bridge address, TCP port, or API token.

## What HASDR does

- Supports multiple RTL-SDR receivers at the same time.
- Identifies receivers by unique RTL-SDR serial whenever possible.
- Scans arbitrary frequency ranges with `rtl_power`.
- Returns complete spectrum bins on demand.
- Calculates peak frequency, peak power, median noise floor, and threshold crossings.
- Runs continuous `rtl_433` decoding.
- Preserves the original `rtl_433` JSON payload.
- Adds normalized frequency metadata for Home Assistant.
- Locks each receiver to one active job so two processes cannot seize the same dongle.
- Allows independent jobs on different SDRs.
- Publishes decoded packets and scan results as Home Assistant events.
- Provides receiver devices, sensors, diagnostics, system health, and native Home Assistant actions.
- Keeps transient RF detections out of the entity registry unless they represent an actual persistent receiver entity.

## Setup experience

Install the integration and choose:

### Local RTL-SDR hardware — recommended

This is the normal option.

#### Home Assistant OS / Supervised

HASDR automatically:

1. Adds the HASDR App repository to Supervisor when needed.
2. Installs the **HASDR SDR Engine** App.
3. Generates an internal API token.
4. Configures the App.
5. Gives the App raw USB and udev access.
6. Starts the App.
7. Marks it as managed by the HASDR config entry.
8. Connects to it over Home Assistant's private App network.

There is no host, port, or token configuration exposed to the user.

The managed App does **not** publish its API port onto the host/LAN.

#### Home Assistant Container

HASDR runs the SDR engine directly inside the Home Assistant process when the container already provides:

- `librtlsdr`
- `rtl_power`
- `rtl_433`
- access to the RTL-SDR USB devices, normally through `/dev/bus/usb`

No TCP bridge is used in this mode.

Home Assistant Container intentionally does not receive the Docker socket or permission to create sibling containers. HASDR will therefore never silently grant itself host-level Docker control just to provision SDR dependencies.

If the native runtime is missing, setup reports that explicitly instead of asking for bridge credentials.

### Remote HASDR host — advanced

This mode is for people who intentionally run the HASDR SDR Engine on another Linux host.

Only this advanced setup asks for:

- host
- port
- API token
- optional HTTPS

The standalone engine remains available under `bridge/` and `docker-compose.yml`.

## Home Assistant installation

For HACS/custom integration use, install the repository and restart Home Assistant.

The integration domain is:

```text
rtl_sdr
```

Then go to:

**Settings → Devices & services → Add integration → RTL-SDR**

Choose **Local RTL-SDR hardware (recommended)**.

## Multiple receivers

Each physical receiver becomes its own Home Assistant device.

Unique EEPROM serials are strongly recommended. Cheap RTL-SDRs often ship with the same default serial. Duplicate serials fall back to USB index, which can change after reboot.

Example:

```bash
rtl_eeprom -d 0 -s 00000001
rtl_eeprom -d 1 -s 00000002
```

Unplug and reconnect the receivers after changing EEPROM values.

## Home Assistant actions

### `rtl_sdr.scan`

Run a one-shot spectrum sweep.

```yaml
action: rtl_sdr.scan
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  radio_id: serial:00000001
  start_frequency_hz: 900000000
  end_frequency_hz: 930000000
  bin_width_hz: 25000
  integration_seconds: 1
  ppm: 0
  bias_tee: false
  threshold_db_above_noise: 10
```

The scan engine records:

- requested range
- bin count
- sample count
- peak frequency
- peak power
- median noise floor
- calculated detection threshold
- threshold crossings
- complete raw spectrum bins

HASDR exposes `rtl_power` values as generic dB power readings. It does not pretend they are calibrated dBm without an explicit calibration layer.

### `rtl_sdr.get_last_scan`

Return the latest complete spectrum scan, including all raw bins.

```yaml
action: rtl_sdr.get_last_scan
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  radio_id: serial:00000001
response_variable: spectrum
```

The full scan is returned under:

```text
spectrum.scan
```

Large raw spectra are retrieved this way rather than dumped onto Home Assistant's event bus.

### `rtl_sdr.start_decoder`

Start continuous `rtl_433` decoding.

```yaml
action: rtl_sdr.start_decoder
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  radio_id: serial:00000001
  frequencies_hz:
    - 433920000
  sample_rate_hz: 250000
  hop_seconds: 15
  ppm: 0
  bias_tee: false
```

Multiple frequencies can be supplied. The decoder hops between them using `hop_seconds`.

Optional `protocols` restricts decoding to specific `rtl_433 -R` protocol numbers.

### `rtl_sdr.stop`

Stop the active job on one receiver.

```yaml
action: rtl_sdr.stop
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  radio_id: serial:00000001
```

### `rtl_sdr.refresh_radios`

Force immediate receiver re-enumeration.

## Home Assistant events

### `rtl_sdr_decoded_packet`

Fired for decoded `rtl_433` packets. The original decoder JSON is preserved under `packet`.

When frequency metadata exists HASDR also adds:

- `frequency_hz`
- `frequency_mhz`

### `rtl_sdr_signal_detected`

Fired for decoded RF packets.

### `rtl_sdr_scan_complete`

Fired after a spectrum scan with a bounded summary. Use `rtl_sdr.get_last_scan` for the complete bin array.

### `rtl_sdr_job_error`

Fired when the SDR process or hardware reports an error. Native diagnostic text is preserved.

## Managed HASDR SDR Engine

The Supervisor App lives in:

```text
hasdr_engine/
```

It is a valid Home Assistant App repository entry and uses:

```yaml
usb: true
udev: true
```

The App installs the native RTL-SDR runtime inside its own container and exposes TCP 8099 only on Home Assistant's internal App network.

The integration provisions it through the Supervisor API and marks it as system-managed by the corresponding config entry.

## Embedded Container runtime

When Home Assistant is running as a normal Docker container and the required native runtime is already present, HASDR uses:

```text
custom_components/rtl_sdr/runtime/
```

directly.

The embedded runtime and managed App use the same radio/job model:

```text
RadioManager
  ├── receiver enumeration
  ├── per-radio locking
  ├── rtl_power jobs
  ├── rtl_433 jobs
  ├── spectrum parsing
  └── decoded packet normalization
```

This is deliberate: the Home Assistant layer does not need different scanner behavior depending on the installation type.

## Advanced standalone engine

The standalone remote-host deployment remains under:

```text
bridge/
docker-compose.yml
scripts/install-bridge.sh
```

It is not required for normal Home Assistant OS/Supervised use.

The remote API includes:

```text
GET  /v1/health
GET  /v1/radios
POST /v1/radios/refresh
GET  /v1/jobs
GET  /v1/radios/{radio_id}/history
GET  /v1/radios/{radio_id}/scan/latest
POST /v1/radios/{radio_id}/scan
POST /v1/radios/{radio_id}/decode
POST /v1/radios/{radio_id}/stop
GET  /v1/ws
```

Remote APIs require:

```text
Authorization: Bearer <HASDR_API_TOKEN>
```

The compatibility environment variable `SDR_BRIDGE_TOKEN` is still accepted by the engine, but new deployments should use `HASDR_API_TOKEN`.

## Security

- Normal local setup does not expose an SDR API to the LAN.
- Supervisor-managed mode communicates over Home Assistant's private App network.
- A random 256-bit API token is generated by the integration for the managed App.
- The managed token is not requested from the user.
- Direct Container mode uses in-process communication rather than TCP.
- Remote mode requires bearer-token authentication.
- Tokens are compared with constant-time comparison by the engine.
- API input ranges, frequency counts, and spectrum-bin counts are bounded.
- One receiver cannot be seized by two simultaneous jobs.
- HASDR is receive-only.
- HASDR does not attempt to decrypt protected communications.
- Home Assistant Container mode does not require or request access to the Docker socket.

## Repository layout

```text
custom_components/rtl_sdr/   Home Assistant integration
hasdr_engine/                Supervisor-managed SDR Engine App
bridge/                      advanced standalone/remote engine
tests/                       backend regression tests
docker-compose.yml           advanced remote deployment
repository.yaml              Home Assistant App repository metadata
hacs.json                    HACS metadata
```

## Current version

**0.2.0**

The 0.2 architecture changes HASDR from a manually configured bridge integration to a local-first Home Assistant integration with an automatically managed Supervisor backend and an embedded Container backend.
