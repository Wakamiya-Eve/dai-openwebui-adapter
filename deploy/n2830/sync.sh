#!/usr/bin/env bash
set -Eeuo pipefail
exec /opt/dai-openwebui-adapter/venv/bin/python \
  -m dai_openwebui_adapter.sync \
  --config /etc/dai-openwebui-adapter/adapter.yaml
