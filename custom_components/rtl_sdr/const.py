"""Constants for RTL-SDR for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "rtl_sdr"
NAME = "RTL-SDR"
VERSION = "0.3.0"

CONF_MODE = "mode"
CONF_ADDON_SLUG = "addon_slug"
CONF_PORT = "port"
CONF_TOKEN = "token"
CONF_USE_SSL = "use_ssl"

MODE_LOCAL = "local"
MODE_SUPERVISOR = "supervisor"
MODE_REMOTE = "remote"

DEFAULT_PORT = 8099

EVENT_DECODED_PACKET = "rtl_sdr_decoded_packet"
EVENT_SCAN_COMPLETE = "rtl_sdr_scan_complete"
EVENT_SIGNAL_DETECTED = "rtl_sdr_signal_detected"
EVENT_JOB_ERROR = "rtl_sdr_job_error"

SERVICE_SCAN = "scan"
SERVICE_START_MONITOR = "start_monitor"
SERVICE_START_DECODER = "start_decoder"
SERVICE_STOP = "stop"
SERVICE_REFRESH_RADIOS = "refresh_radios"
SERVICE_GET_LAST_SCAN = "get_last_scan"

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.BUTTON]
