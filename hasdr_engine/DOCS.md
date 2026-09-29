# HASDR SDR Engine

This App is the managed hardware backend for the **RTL-SDR / HASDR** Home Assistant integration.

In normal use you do not configure or install this App manually. When Home Assistant Supervisor is available, the HASDR integration installs, configures, starts, updates, and communicates with this App automatically.

## Hardware

The App receives raw USB access from Home Assistant Supervisor and supports multiple RTL-SDR compatible receivers. Unique RTL-SDR serial numbers are strongly recommended when more than one receiver is attached.

## Networking

The HASDR API listens on TCP 8099 only inside the Home Assistant App network. No host/LAN port is published by default.

## Security

The Home Assistant integration generates and configures the API token automatically. The token is stored as an App password option and is not intended for manual use.

## Manual installation

Manual installation is only intended for development or recovery. Install the App from the HASDR repository, set a strong random API token, and start the App.
