# RTL-SDR for Home Assistant

A local-first Home Assistant integration and companion bridge for one or more RTL-SDR receivers.

The project deliberately separates USB/radio ownership from Home Assistant. The bridge owns the SDR hardware and native command-line tools; Home Assistant connects to the bridge over an authenticated local API and WebSocket.

## v0.1 capabilities

- Multiple simultaneous RTL-SDR receivers.
- Receiver identity by RTL-SDR serial number when the serial is unique.
- One-shot arbitrary range scans using `rtl_power`.
- Full spectrum bins returned for each scan, plus peak frequency, peak power, median noise floor, and threshold crossings.
- Continuous `rtl_433` decoding on one or multiple frequencies.
- Decoder output is preserved as JSON and augmented with normalized frequency fields.
- Frequency, RSSI, SNR, noise, protocol and model metadata from `rtl_433` when available.
- Per-radio job locking so two processes cannot seize the same dongle.
- Separate jobs can run simultaneously on different dongles.
- Live WebSocket events from the bridge to Home Assistant.
- Home Assistant sensors for radio status, active job, last spectrum peak/noise values and decoded packet information.
- Home Assistant events for decoded packets, detected signals, completed scans and job errors.
- API bearer-token authentication.
- Docker deployment with no added Linux capabilities and a read-only filesystem.

## Architecture

```text
Home Assistant
  custom_components/rtl_sdr
          |
          | HTTP + WebSocket, bearer token
          v
RTL-SDR Bridge :8099
  |-- librtlsdr device enumeration
  |-- rtl_power range scans
  |-- rtl_433 protocol decoding
  |
  +-- RTL-SDR #1
  +-- RTL-SDR #2
  +-- RTL-SDR #N
```

## Why a bridge?

RTL-SDR access depends on native `librtlsdr`, libusb and native radio tools. Keeping those outside the Home Assistant process avoids polluting or destabilizing the Home Assistant Python environment and allows the radio host to be the same machine or a different Linux host.

## Bridge installation

Requirements:

- Linux host with Docker and Docker Compose v2.
- One or more RTL-SDR compatible USB receivers.

Clone the repository and run:

```bash
git clone https://github.com/gigabytegrove/hasdr.git
cd hasdr
./scripts/install-bridge.sh
```

The installer creates a local `.env` containing a random 256-bit API token and starts the bridge on TCP port `8099`.

To see the token:

```bash
cat .env
```

The token is deliberately excluded by `.gitignore`.

### Duplicate factory serial numbers

Many inexpensive RTL-SDR dongles ship with the same serial. The bridge will still expose those units by USB index, but USB index is not guaranteed to remain stable across reboots. For reliable multi-SDR operation, assign each dongle a unique serial with `rtl_eeprom`, for example:

```bash
rtl_eeprom -d 0 -s 00000001
rtl_eeprom -d 1 -s 00000002
```

Unplug and reconnect the receivers after changing EEPROM values.

## Home Assistant installation

Copy:

```text
custom_components/rtl_sdr
```

to:

```text
/config/custom_components/rtl_sdr
```

Restart Home Assistant, then open **Settings > Devices & services > Add integration > RTL-SDR**.

Enter:

- Bridge host or IP
- Port `8099`
- `SDR_BRIDGE_TOKEN` from the bridge `.env`
- HTTPS only when the bridge is placed behind a TLS reverse proxy

Each attached receiver appears as a separate Home Assistant device.

## Actions

### `rtl_sdr.scan`

Runs one spectrum sweep and returns its result asynchronously through `rtl_sdr_scan_complete`.

```yaml
action: rtl_sdr.scan
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

The completed event contains the complete list of spectrum bins plus derived values such as `peak_frequency_hz`, `peak_power_db`, `noise_floor_db`, `detection_threshold_db`, and `detections`.

`rtl_power` values are exposed as generic dB power readings. The integration does not label them calibrated dBm unless a future calibration layer explicitly provides that guarantee.

### `rtl_sdr.start_decoder`

Starts continuous `rtl_433` decoding.

```yaml
action: rtl_sdr.start_decoder
config_entry_id: YOUR_CONFIG_ENTRY_ID
radio_id: serial:00000001
frequencies_hz:
  - 433920000
sample_rate_hz: 250000
hop_seconds: 15
ppm: 0
bias_tee: false
```

Multiple frequencies can be supplied. `rtl_433` will hop between them using `hop_seconds`.

Optional `protocols` restricts decoding to specific `rtl_433 -R` protocol numbers.

### `rtl_sdr.get_last_scan`

Returns the latest complete spectrum result as Home Assistant action response data. This is the supported way to retrieve the full `bins` and `detections` arrays without placing a potentially large spectrum payload on the Home Assistant event bus.

```yaml
action: rtl_sdr.get_last_scan
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  radio_id: serial:00000001
response_variable: spectrum
```

The result is available under `spectrum.scan`.

### `rtl_sdr.stop`

Stops the active process on one receiver.

```yaml
action: rtl_sdr.stop
config_entry_id: YOUR_CONFIG_ENTRY_ID
radio_id: serial:00000001
```

### `rtl_sdr.refresh_radios`

Forces immediate hardware re-enumeration. The bridge also checks for hot-plug changes automatically.

## Home Assistant events

### `rtl_sdr_decoded_packet`

Contains the untouched `rtl_433` JSON under `packet` with normalized `frequency_hz` and `frequency_mhz` added when frequency metadata exists.

### `rtl_sdr_signal_detected`

Fired for decoded RF packets. Spectrum-scan threshold crossings are included in the `rtl_sdr_scan_complete` result.

### `rtl_sdr_scan_complete`

Contains a bounded summary of the completed one-shot spectrum result. Use `rtl_sdr.get_last_scan` to retrieve the complete raw bin and detection arrays.

### `rtl_sdr_job_error`

Contains subprocess or receiver errors without hiding the native tool's diagnostic text.

## Bridge API

All endpoints require:

```text
Authorization: Bearer <SDR_BRIDGE_TOKEN>
```

Endpoints:

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

The last 5 completed scans per radio are retained in bridge memory and available from the history endpoint. They are intentionally not persisted to disk in v0.1.

## Security

- The API refuses to start without a token of at least 16 characters.
- Tokens are compared with constant-time comparison.
- The bridge container runs read-only, drops all Linux capabilities, enables `no-new-privileges`, and receives only USB device-node access through Docker's USB cgroup rule.
- The bridge does not expose raw shell execution through its API.
- Request ranges, bin counts, frequency counts and other user-controlled values are bounded.
- Never expose port 8099 directly to the public internet. Use a private LAN/VPN or a properly authenticated TLS reverse proxy.

## Current scope

v0.1 is receive-only. It does not transmit RF. It does not attempt to decrypt protected communications. General spectrum discovery and supported unencrypted protocol decoding are the intended use cases.

Potential later additions include waterfall history, saved scan profiles, automatic entity promotion for stable decoded transmitter IDs, raw IQ capture, `rtl_fm`, ADS-B backends, SoapySDR hardware and additional protocol-specific decoders.
