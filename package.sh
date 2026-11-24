#!/usr/bin/env bash
# Packs skill/ (and a profile) into a zip for claude.ai. See pipeline/package.py.
set -euo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
exec python3 -m pipeline.package "$@"
