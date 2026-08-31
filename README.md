# Секонд-Бот

Telegram-бот, который хранит каталог секонд-хендов Севастополя и
уведомляет пользователей о ключевых днях цикла каждого магазина:
день завоза, день максимальной скидки, середина цикла. Для магазинов
без расписания — уведомления по выбранным дням недели.

## Стек

- Python 3.11, aiogram 3
- PostgreSQL 15 (asyncpg)
- APScheduler (минутный шедулер)
- httpx + selectolax (парсер сайта Megahand)

## Быстрый старт (Docker)

```bash
cp .env.example .env
# заполнить BOT_TOKEN и ADMIN_IDS (как минимум)
docker compose up -d --build
docker compose logs -f bot
```

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env  # заполнить BOT_TOKEN, DATABASE_URL
python bot.py
```

## Переменные окружения

| Переменная | Обяз. | Назначение |
|---|---|---|
| `BOT_TOKEN` | да | Токен Telegram-бота |
| `DATABASE_URL` | да | postgres://user:pass@host:5432/db |
| `ADMIN_IDS` | да | TG id админов через запятую |
| `ADMIN_CHAT_ID` | нет | Куда форвардить feedback и ошибки |
| `LOG_LEVEL` | нет | INFO по умолчанию |

## Команды

**Пользователь**
- `/start` — главное меню
- `/help` — справка
- `/feedback` — связаться с админом

**Админ**
- `/list_shops` — список магазинов с id
- `/add_shop` — добавить (мастер)
- `/delete_shop <id>` — удалить
- `/set_arrival <id> <YYYY-MM-DD>` — обновить anchor одного магазина
- `/set_chain_arrival <Chain> <YYYY-MM-DD>` — обновить anchor всей сети
- `/import_megahand` — спарсить anchor с sevastopol.mhand.ru/promo/
- `/broadcast` — рассылка всем активным
- `/stats` — статистика

## Структура

```
bot.py                    # entrypoint
config.py                 # .env loader
data/
  db.py                   # asyncpg pool, схема
  seed.py                 # сид магазинов Севастополя
  repos/                  # users, shops, subs, notifier_repo, admin_repo, feedback_repo
handlers/                 # start, catalog, my_shops, settings, admin, feedback, errors
keyboards/
states/
services/
  cycle.py                # day_in_cycle, events_on, next_event_date
  card_render.py          # рендер карточки магазина
  notifier.py             # сборка триггеров и отправка
  scheduler.py            # APScheduler с догоном
  megahand_parser.py
middlewares/throttling.py
tests/                    # 59 тестов на чистую логику
```

## Тесты

```bash
python -m pytest tests/ -q
```

## Логика циклов

- `cycle_length` (14 / 21) и `anchor_date` задают цикл магазина.
- `day_in_cycle = (today - anchor_date).days % cycle_length`.
- День 0 → завоз, последний день → макс. скидка,
  `cycle_length // 2` → середина.
- Магазины без `cycle_length` уведомляют по дням недели из
  `notification_weekdays`.

## Шедулер

Тикает каждую минуту. На минуту HH:MM выбирает пользователей с
`notify_time = HH:MM`, у которых нет активной паузы. Для каждого
собирает события сегодняшнего дня по подпискам, дедуплицирует
через `sent_notifications`, отправляет одно сгруппированное сообщение
и отмечает отправку. При старте догоняет пропущенные минуты в пределах
последних 2 часов и текущих суток.
```
