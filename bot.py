"""VK-версия «Зоны»: точка входа.

Использование:
    python run_vk.py                 # режим из VK_USE_CALLBACK
    python run_vk.py --webhook       # Callback API (webhook) принудительно
    python run_vk.py --longpoll      # Long Poll принудительно

Переменные окружения (.env):
    VK_TOKEN    — токен сообщества (права: сообщения, фото, документы)
    VK_GROUP_ID — id сообщества
    DATABASE_URL / DB_PATH — база (как в телеграм-версии)
    ADMIN_IDS   — VK user id админов через запятую
"""

import logging
import sys

from app.bot import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Флаг --webhook включает Callback API (webhook), --longpoll форсирует Long Poll
    # ДАЖЕ если в env стоит VK_USE_CALLBACK=1; без аргументов — режим из env.
    force_webhook: bool | None = None
    if "--webhook" in sys.argv:
        force_webhook = True
    elif "--longpoll" in sys.argv:
        force_webhook = False
    try:
        main(use_webhook=force_webhook)
    except KeyboardInterrupt:
        print("Остановлено", file=sys.stderr)
        sys.exit(0)
