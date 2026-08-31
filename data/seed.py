import logging
from datetime import date

from data.db import pool

log = logging.getLogger(__name__)


# Якорная дата для еженедельных магазинов без явной delivery_anchor.
# Жёстко привязана к будням (Пн = 2026-01-05) — циклы корректно считаются.
_WEEKLY_ANCHOR = {
    0: date(2026, 1, 5),   # Пн
    1: date(2026, 1, 6),   # Вт
    2: date(2026, 1, 7),   # Ср
    3: date(2026, 1, 8),   # Чт
    4: date(2026, 1, 9),   # Пт
    5: date(2026, 1, 10),  # Сб
    6: date(2026, 1, 11),  # Вс
}


SEED_SHOPS: list[dict] = [
    {
        "name": "Super (5км)",
        "address": "5-й км",
        "description": "Расписание: 9:00–19:00 (Пт до 14:00)\nКарта: https://yandex.ru/maps/-/CHGjeXP4",
        "chain_name": None,
        "cycle_length": 14,
        "anchor_date": date(2026, 4, 11),
        "price_start": 2039,
        "price_step": 200,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/1969018/2a00000170482b6064997fd0cf71d522297b/XXXL",
    },
    {
        "name": "Favorite (Лебедя)",
        "address": "ТЦ Лебедь",
        "description": "Расписание: 9:00–20:00 (Чт до 12:00)\nVK: https://vk.com/club12975102\nКарта: https://yandex.ru/maps/-/CHGjiQzZ",
        "chain_name": "Favorite",
        "cycle_length": 14,
        "anchor_date": date(2026, 4, 10),
        "price_start": 2299,
        "price_step": 200,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/4508101/2a0000017846eadacf3327984530600685fb/XXXL",
    },
    {
        "name": "Megahand (Океан)",
        "address": "ТРЦ Океан",
        "description": "Расписание: 9:00–21:00\nОсобенность: цена за вещь (не поштучно)\nКарта: https://yandex.ru/maps/-/CHGjiYIr",
        "chain_name": None,
        "cycle_length": 21,
        "anchor_date": date(2026, 4, 4),
        "price_start": None,
        "price_step": None,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/6514890/2a0000018a75483ed9ae02e7d824626a8499/XXXL",
    },
    {
        "name": "Евростиль (Московский рынок)",
        "address": "Московский рынок",
        "description": "Расписание: 9:30–19:30\nЗавоз в Чт раз в месяц (1-й Чт)\nОсобенность: в день завоза магазин не работает\nКарта: https://yandex.ru/maps/-/CHGjJWle",
        "chain_name": None,
        "cycle_length": None,
        "anchor_date": None,
        "price_start": 3100,
        "price_step": 100,
        "photo_file_id": None,
    },
    {
        "name": "Favorite (ПОР)",
        "address": "Проспект Октябрьской Революции",
        "description": "Расписание: 9:00–20:00 (Вт до 12:00)\nVK: https://vk.com/club12975102\nКарта: https://yandex.ru/maps/-/CHGjNSJq",
        "chain_name": "Favorite",
        "cycle_length": 7,
        "anchor_date": _WEEKLY_ANCHOR[2],
        "price_start": 2499,
        "price_step": 200,
        "photo_file_id": None,
    },
    {
        "name": "БлагоДар (ПОР)",
        "address": "Проспект Октябрьской Революции",
        "description": "Расписание: 10:00–18:00\nЗавозов нет — хозяйка сама приносит вещи, цена за штуку\nКарта: https://yandex.ru/maps/-/CHGjNH88",
        "chain_name": None,
        "cycle_length": None,
        "anchor_date": None,
        "price_start": None,
        "price_step": None,
        "photo_file_id": None,
    },
    {
        "name": "Euro (Секонд Полины)",
        "address": "Секонд Полины",
        "description": "Расписание: 9:00–19:00 (Вс 10:00–16:00)\nКарта: https://yandex.ru/maps/-/CHGrMI53",
        "chain_name": None,
        "cycle_length": 14,
        "anchor_date": date(2026, 4, 8),
        "price_start": 2199,
        "price_step": 200,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/5253303/2a0000017b1cdcab18cf780e0f03ebe8887b/XXXL",
    },
    {
        "name": "Секонд хэнд (На Омеге)",
        "address": "Омега",
        "description": "Расписание: 10:00–19:00 (Пн — выходной, Вс до 17:00)\nКарта: https://yandex.ru/maps/-/CHGrQIM1",
        "chain_name": None,
        "cycle_length": 7,
        "anchor_date": _WEEKLY_ANCHOR[1],
        "price_start": 1999,
        "price_step": 200,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/4667561/2a00000178c401d684e47c9467062a7c5109/XXXL",
    },
    {
        "name": "Винтаж (На Казачке)",
        "address": "Казачка",
        "description": "Расписание: 10:00–19:00\nЗавоз в Пт раз в месяц (2-я неделя)\nКарта: https://yandex.ru/maps/-/CHGrQ6ZN",
        "chain_name": None,
        "cycle_length": None,
        "anchor_date": None,
        "price_start": 2800,
        "price_step": 150,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/1975185/2a0000016eeea2cc5e0638d157c371a36434/XXXL",
    },
    {
        "name": "Evro Hand (Проспект Победы)",
        "address": "Проспект Победы",
        "description": "Расписание: 10:00–19:00\nКарта: https://yandex.ru/maps/-/CHGrUAmk",
        "chain_name": None,
        "cycle_length": 14,
        "anchor_date": date(2026, 4, 9),
        "price_start": 2599,
        "price_step": 200,
        "photo_file_id": None,
    },
    {
        "name": "Сток (Проспект Победы)",
        "address": "Проспект Победы",
        "description": "Расписание: 10:00–18:00 (Вс — выходной)\nКарта: https://yandex.ru/maps/-/CHGrUT35",
        "chain_name": None,
        "cycle_length": 21,
        "anchor_date": date(2026, 4, 11),
        "price_start": 2399,
        "price_step": 150,
        "photo_file_id": None,
    },
    {
        "name": "Second Hand (Проспект Победы)",
        "address": "Проспект Победы",
        "description": "Расписание: 9:00–19:00\nКарта: https://yandex.ru/maps/-/CHGrYY--",
        "chain_name": None,
        "cycle_length": 7,
        "anchor_date": _WEEKLY_ANCHOR[0],
        "price_start": 2099,
        "price_step": 200,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/1870294/2a00000175874b9ba812b9f0ea31fdf2bb8e/XXXL",
    },
    {
        "name": "Добрый Покупатель (Новый бульвар)",
        "address": "Новый бульвар",
        "description": "Расписание: 10:00–19:00\nКарта: https://yandex.ru/maps/-/CHGrYW9q",
        "chain_name": None,
        "cycle_length": 14,
        "anchor_date": date(2026, 4, 15),
        "price_start": 2299,
        "price_step": 200,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/3954938/2a00000175874b05384673d9107796aa3410/XXXL",
    },
    {
        "name": "Городской Second Hand №1 (Комсомолка)",
        "address": "Комсомолка",
        "description": "Расписание: 9:00–19:00 (Ср до 15:00)\nЗавоз в Чт раз в месяц (1-я неделя)\nОсобенность: завоз нестабильный — бывают пропуски\nКарта: https://yandex.ru/maps/-/CHGr4O96",
        "chain_name": None,
        "cycle_length": None,
        "anchor_date": None,
        "price_start": 2900,
        "price_step": 100,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/2815220/2a0000017322dc952e3dfe94577164122167/XXXL",
    },
    {
        "name": "Евро Секонд Хенд (Центральный рынок)",
        "address": "Центральный рынок",
        "description": "Расписание: 9:00–18:00 (Пн до 13:00)\nКарта: https://yandex.ru/maps/-/CHGraEIN",
        "chain_name": None,
        "cycle_length": 14,
        "anchor_date": date(2026, 4, 14),
        "price_start": 2500,
        "price_step": 100,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/4289674/2a0000017b7a09556685a931872e3783198f/XXXL",
    },
    {
        "name": "БУтик (На Северной)",
        "address": "Северная сторона",
        "description": "Расписание: 9:00–20:00\nКарта: https://yandex.ru/maps/-/CHGrqRnj",
        "chain_name": None,
        "cycle_length": 14,
        "anchor_date": date(2026, 4, 17),
        "price_start": 2199,
        "price_step": 200,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/3511135/2a00000179363615fed03d6db9c6e5e37ee4/XXXL",
    },
    {
        "name": "Евро Стиль (На Восставших)",
        "address": "Площадь Восставших",
        "description": "Расписание: 9:00–18:45\nЗавоз в Чт раз в месяц (последняя неделя), стабильно\nVK: https://vk.com/evrostilsevas\nКарта: https://yandex.ru/maps/-/CHGruF8G",
        "chain_name": None,
        "cycle_length": None,
        "anchor_date": None,
        "price_start": 3100,
        "price_step": 100,
        "photo_file_id": None,
    },
    {
        "name": "Еврохенд (На Пожарова)",
        "address": "Пожарова",
        "description": "Расписание: 10:00–20:00\nКарта: https://yandex.ru/maps/-/CHG-MCn9",
        "chain_name": None,
        "cycle_length": 7,
        "anchor_date": _WEEKLY_ANCHOR[0],
        "price_start": 1899,
        "price_step": 200,
        "photo_file_id": None,
    },
    {
        "name": "Благотворительный СХ (Техбиблиотека)",
        "address": "Техбиблиотека",
        "description": "Расписание: 10:00–19:00\nЗавозов нет — вещи приносят люди, цена фиксированная\nКарта: https://yandex.ru/maps/?whatshere%5Bzoom%5D=20&whatshere%5Bpoint%5D=33.517743,44.583220",
        "chain_name": None,
        "cycle_length": None,
        "anchor_date": None,
        "price_start": None,
        "price_step": None,
        "photo_file_id": "https://avatars.mds.yandex.net/get-altay/11401274/2a0000018d212dd8efacead351efa5f5c6f7/XXXL",
    },
]


async def seed_shops_if_empty() -> None:
    async with pool().acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM shops")
        if count and int(count) > 0:
            return
        for s in SEED_SHOPS:
            shop_id = int(await conn.fetchval(
                """
                INSERT INTO shops (
                    name, address, description, chain_name,
                    cycle_length, anchor_date, price_start, price_step
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                s["name"], s["address"], s["description"],
                s["chain_name"], s["cycle_length"], s["anchor_date"],
                s["price_start"], s["price_step"],
            ))
            # Фото теперь хранятся только в shop_photos. Если у seed-записи
            # есть photo_file_id (Telegram file_id или URL — answer_photo
            # принимает оба) — добавляем его как первое фото.
            photo = s.get("photo_file_id")
            if photo:
                await conn.execute(
                    "INSERT INTO shop_photos (shop_id, file_id, ord) VALUES ($1, $2, 0)",
                    shop_id, photo,
                )
    log.info("seeded %d shops", len(SEED_SHOPS))
