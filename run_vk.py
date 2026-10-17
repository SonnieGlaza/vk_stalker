"""VK-версия «Зоны»: точка входа.

Использование:
    python run_vk.py                  # Long Poll
    python run_vk.py --webhook        # Callback API (webhook POST /callback)

Переменные окружения (.env):
    VK_TOKEN          — токен сообщества (права: сообщения, фото, документы)
    VK_GROUP_ID       — id сообщества
    DATABASE_URL / DB_PATH — база (как в телеграм-версии)
    ADMIN_IDS         — VK user id админов через запятую
    VK_USE_CALLBACK=1 — режим Callback API (или аргумент --webhook)
    VK_WEBHOOK_PORT, VK_CONFIRMATION_CODE, VK_CALLBACK_SECRET — для webhook
"""

import argparse
import logging
import sys


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VK-бот «Зона: Сталкер»")
    parser.add_argument("--webhook", action="store_true", help="Callback API вместо Long Poll")
    args, _ = parser.parse_known_args()

    from app.bot import main

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        main(use_webhook=args.webhook)
    except KeyboardInterrupt:
        print("Остановлено", file=sys.stderr)
        sys.exit(0)