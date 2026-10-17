#!/usr/bin/env bash
set -euo pipefail

# Callback API (webhook) — режим по умолчанию для хостинга.
# Флаг --webhook включает webhook даже без VK_USE_CALLBACK=1.
python3 run_vk.py --webhook