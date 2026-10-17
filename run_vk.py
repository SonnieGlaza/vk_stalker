"""VK-версия «Зоны»: точка входа.

Использование:
    python run_vk.py

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
    try:
        main()
    except KeyboardInterrupt:
        print("Остановлено", file=sys.stderr)
        sys.exit(0)