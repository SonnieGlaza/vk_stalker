## Статус: код готов ✅

Уже реализовано и проверено:
- `app/vk_platform.py` — `run_webhook(settings, handle_event, background)`: aiohttp-сервер
  `POST /callback`, подтверждение (`confirmation`), проверка секрета, обработка
  `message_new`/`message_event` через тот же диспетчер, `market_order_new`;
- `app/bot.py` — `handle_market_order` (автоподтверждение топ-апа по цене,
  рассогласование → уведомление админу, заявка остаётся) + `main(use_webhook=...)`;
- `run_vk.py --webhook` / `VK_USE_CALLBACK=1` — выбор режима;
- `app/config.py` — `VK_USE_CALLBACK`, `VK_WEBHOOK_PORT`, `VK_CONFIRMATION_CODE`,
  `VK_CALLBACK_SECRET`; в `requirements.txt` добавлен `aiohttp`.

## Осталось — настройка группы VK (завтра)

1. Управление → Работа с API → Callback API → «Новый сервер»:
   адрес `https://<домен>/callback` (нужен публичный HTTPS URL, например на Railway).
2. Скопировать «ключ подтверждения» в `.env` → `VK_CONFIRMATION_CODE`,
   «секретный ключ» → `VK_CALLBACK_SECRET`.
3. Подписки: «Сообщения» (входящие), «Нажатия по inline-кнопкам» (message_event),
   «Покупка товара» (market_order_new), при необходимости «Вступление/выход».
4. Включить VK Market группы и опубликовать товары: 149₽ / 249₽ / 499₽ / 999₽
   (соответствуют пакетам `TopUP_PACKAGES` в `app/bot.py`).
5. Запуск: `python run_vk.py --webhook` (порт `VK_WEBHOOK_PORT`, по умолчанию 8080).
6. Проверка: сообщение в группу, нажатие кнопки, тестовая покупка товара
   → игроку приходит «⭐ … оплачен и зачислен».

## Как это работает

- `confirmation` → ответ кодом подтверждения;
- `message_new` / `message_event` → конвертация в `VkEvent` и вызов `handle_event`
  (тот же диспетчер, что и Long Poll — кнопки/пейлоады не меняются);
- `market_order_new` → `handle_market_order`: берём `user_id` покупателя, находим
  его заявку `topup:pending:{id}`, сверяем цену пакета с `total` заказа
  (копейки/100), зачисляем через `_apply_topup_payload` и пишем игроку;
  при несовпадении цены — заявка остаётся, админ получает уведомление
  (ручной `confirm_topup` остаётся как запасной путь);
- фоновый тик `_zone_tick` запускается и в webhook-режиме.