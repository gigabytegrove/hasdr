FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache \
    python3 \
    py3-aiohttp \
    librtlsdr \
    rtl-sdr \
    rtl_433

WORKDIR /opt/hasdr
COPY sdr_bridge ./sdr_bridge
COPY run.sh /run.sh
RUN chmod 0755 /run.sh

EXPOSE 8099
CMD ["/run.sh"]
