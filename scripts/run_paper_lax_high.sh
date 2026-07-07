#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
kalshi-weather run-paper --series KXHIGHLAX --station KLAX --interval-seconds 60
