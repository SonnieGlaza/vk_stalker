#!/usr/bin/env bash
set -euo pipefail

# VK-бот: Callback API (webhook). Для Long Poll убери флаг --webhook.
python3 run_vk.py --webhook
