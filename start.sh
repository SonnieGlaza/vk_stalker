#!/usr/bin/env bash
set -euo pipefail

# Long Poll API — бот сам ходит к VK, входящий адрес/вебхук не нужен.
# Перед запуском включи в группе: Настройки → Сообщения → «Сообщения сообщества: Включены»
# и Настройки → Работа с API → Long Poll API → «Включён» (см. README).
python3 run_vk.py --longpoll