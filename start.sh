#!/usr/bin/env bash
set -euo pipefail

# Callback API (webhook) — мгновенная доставка событий от VK.
# Адрес для VK: https://<public-domain>/callback (см. лог при старте).
# Альтернатива без домена: python3 run_vk.py --longpoll (доставка ~40-60 с).
python3 run_vk.py --webhook