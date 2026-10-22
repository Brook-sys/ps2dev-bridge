# ps2dev-bridge — official ps2dev toolchain container + MCP/REST debug bridge.
#
# Base image contents (per ps2dev release notes): EE/IOP/DVP toolchains,
# ps2sdk, gsKit, ps2sdk-ports, ps2-packer and ps2client.
# Pinned to a release tag for reproducible builds — bump deliberately
# and re-run the workflow.
FROM ghcr.io/ps2dev/ps2dev:v2.0.0

RUN apk add --no-cache python3 py3-pip

WORKDIR /app

# Install the bridge service into an isolated venv (validated on Alpine:
# python3 -m venv ships a working ensurepip + pip).
COPY bridge /app/bridge
RUN python3 -m venv /app/venv \
    && /app/venv/bin/pip install --no-cache-dir /app/bridge \
    && rm -rf /app/bridge

ENV PATH="/app/venv/bin:${PATH}" \
    PS2BRIDGE_HOST=0.0.0.0 \
    PS2BRIDGE_PORT=17800 \
    PS2BRIDGE_WORKSPACE=/work

# Project sources and build artifacts survive container recreation.
VOLUME ["/work"]

EXPOSE 17800

ENTRYPOINT ["python", "-m", "ps2bridge"]
