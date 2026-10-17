from __future__ import annotations

import random
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.avatar_render import render_avatar
from app.faction_ranks import resolve_rank_title
from app.game_logic import (
    ITEM_LABELS,
    armor_defense,
    armor_flat_mitigation,
    effective_max_health,
    equipment_power,
)
from app.skins import resolve_skin
from app.storage import Character, Storage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "DejaVuSans.ttf"
LOCAL_NOTO_FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "NotoSans-Regular.ttf"
FONT_CANDIDATES = (
    str(LOCAL_NOTO_FONT_PATH),
    str(LOCAL_FONT_PATH),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
)


def _font_supports_cyrillic(font: ImageFont.ImageFont) -> bool:
    test_text = "Карточка персонажа"
    try:
        bbox = font.getbbox(test_text)
    except Exception:
        return False
    # Invalid or empty bbox often indicates missing glyphs.
    if bbox is None:
        return False
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if width <= 0 or height <= 0:
        return False
    # Exclude fallback "tofu" glyphs that appear as repeated squares.
    missing_patterns = set()
    for probe in ("□", "\u25a1", "?", "\ufffd"):
        try:
            missing_patterns.add(bytes(font.getmask(probe)))
        except Exception:
            continue
    samples = []
    for probe in ("К", "Я", "Ж", "Ы", "Ч"):
        try:
            samples.append(bytes(font.getmask(probe)))
        except Exception:
            return False
    if not samples:
        return False
    if any(sample in missing_patterns for sample in samples):
        return False
    # Cyrillic glyphs should not all be identical.
    return len(set(samples)) > 1


@lru_cache(maxsize=1)
def _read_local_font_bytes(path: Path) -> bytes | None:
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


@lru_cache(maxsize=8)
def _resolve_font_path() -> tuple[str | None, str]:
    for local_path in (LOCAL_NOTO_FONT_PATH, LOCAL_FONT_PATH):
        local_font = _read_local_font_bytes(local_path)
        if local_font is not None:
            try:
                font = ImageFont.truetype(BytesIO(local_font), size=22)
                if _font_supports_cyrillic(font):
                    return (str(local_path), "локальный файл")
            except OSError:
                pass
    for path in FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            font = ImageFont.truetype(path, size=22)
        except OSError:
            continue
        if _font_supports_cyrillic(font):
            return (path, "системный файл")
    return (None, "встроенный PIL (ограниченный)")


def _load_font(size: int) -> ImageFont.ImageFont:
    resolved_path, _ = _resolve_font_path()
    for local_path in (LOCAL_NOTO_FONT_PATH, LOCAL_FONT_PATH):
        local_font = _read_local_font_bytes(local_path)
        if local_font is not None:
            try:
                font = ImageFont.truetype(BytesIO(local_font), size=size)
                if _font_supports_cyrillic(font):
                    return font
            except OSError:
                continue
    if resolved_path is not None:
        try:
            return ImageFont.truetype(resolved_path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _faction_color(faction: str | None) -> tuple[int, int, int]:
    if faction == "Долг":
        return (190, 70, 65)
    if faction == "Свобода":
        return (70, 165, 90)
    if faction == "Нейтралы":
        return (145, 145, 145)
    if faction == "Бандиты":
        return (165, 120, 55)
    if faction == "Монолит":
        return (190, 175, 55)
    return (110, 110, 130)


def _location_color(location: str) -> tuple[int, int, int]:
    mapping = {
        "Росток": (90, 110, 150),
        "Армейские склады": (80, 120, 85),
        "Кордон": (96, 124, 158),
        "Свалка": (128, 116, 74),
        "НИИ Агропром": (118, 94, 142),
        "Болото": (78, 122, 104),
        "Рыжий лес": (142, 98, 72),
        "Янтарь": (130, 125, 75),
        "Темная долина": (120, 90, 90),
        "Радар": (105, 85, 125),
        "Припять": (110, 70, 70),
        "ЧАЭС": (150, 140, 50),
        "База новичков": (100, 105, 120),
    }
    return mapping.get(location, (95, 95, 95))


# Короткие фразы и мемы: должны полностью помещаться в 3 строки карточки.
STALKER_QUOTES: tuple[str, ...] = (
    # Классика и атмосфера
    "Зона — это не место, это состояние души.",
    "Сидорович, где мой детектор?!",
    "А ну, стоять! Документы... а, свой.",
    "Выброс через час. Шучу. Или нет.",
    "Артефакт в карман, радиацию — в другое место.",
    "Где-то в тумане хрустит сухарик...",
    "Долг — долг, но обед важнее.",
    "Свобода! ...от аптечки, пожалуйста.",
    "Тихо! Слышишь? Это твой инвентарь ломается.",
    "Бандюган, отдай ботинки!",
    "Новичок — не приговор. Выброс вне базы — да.",
    "Отклик нашёл, а артефакт — нет. Классика.",
    "Пусть придёт выброс... и принесёт патроны.",
    "Сахарный деньок? Нет, сегодня солёный.",
    "Я не боюсь кровососов. Я боюсь лагов.",
    "КПК молчит — значит, всё по плану.",
    "Третий день без сна. Зона красивая.",
    "Проводник сказал прямо. Я пошёл налево.",
    "Монолит звал... я не взял трубку.",
    "Хлеб, колбаса, водка — три столпа выживания.",
    "Брат, ты артефакт нашёл или просто гуляешь?",
    "Сталкер идёт туда, где карта не помогает.",
    "Где-то рядом хрустит куст. Это не куст.",
    "Радиация в норме. Норма — понятие растяжимое.",
    "Один в поле — сталкер, если есть аптечка.",
    # Мемы и приколы Зоны
    "Иди отсюда, сталкер!",
    "Два года тишины — и снова выброс.",
    "Схрон: ноль патронов, тысяча надежд.",
    "Выброс? Я и так уже мёртв внутри.",
    "Кровосос — просто очень настойчивый друг.",
    "Аномалия съела мои планы на вечер.",
    "Долг защитит мир от меня.",
    "Свобода — это когда есть хлеб и водка.",
    "Монолит оставил голосовое. Не слушал.",
    "Чёрный экран — это immersion, не баг.",
    "Флешка есть, координат нет.",
    "Бармен знает всё, но молчит.",
    "Сидорович опять поднял цены.",
    "Я шёл за артефактом, нашёл передоз радиации.",
    "Отклик пищит — сердце замирает.",
    "Спавн в аномалии. Классика жанра.",
    "Проводник: налево. Я: *иду направо*",
    "Тихая Зона? Не, не слышал.",
    "STALKER не лагает — это атмосфера.",
    "Где мод на реализм? В Зоне.",
    "Пусть кто-нибудь выйдет в солнечный.",
    "Братан, скинь сейв. Пожалуйста.",
    "Это не баг, это особенность Зоны.",
    "Кабан бежит — я бегу быстрее.",
    "Артефакт нашёл, выбраться не могу.",
    "Патроны кончились, надежда тоже.",
    "Росток — мой второй дом.",
    "Бандиты: отдай шмот. Я: нет.",
    "Выброс за выбросом, как патчи.",
    "Нож и куртка — мой endgame билд.",
    "Детектор Велес — мечта ипотеки.",
    "Арт на живучесть — лучший друг.",
    "Где ты, Тень Чернобыля?",
    "Чистое небо? Не, не знаю.",
    "Играю в сталкера, выживаю в офисе.",
    "А если это был не куст?",
    "Слепой пёс — ночной кошмар.",
    "Сопливый мелкий, но злой.",
    "Контролёр посмотрел. Я отвернулся.",
    "Иван, я тебя в аномалию звать не звал.",
    "Cheeki breeki, но по-нашему: хлеб есть?",
    "Шустрый как слепой кабан в тумане.",
    "Сахарный снайперский деньок, одобряю.",
    "Лут с контейнера: один болт.",
    "Квест: принеси. Я: потерялся.",
    "Водка лечит всё, кроме выброса.",
    "Грузовик купил — дизеля нет.",
    "Спальник есть, спать некогда.",
    "Рейд прошёл, снаряга сломалась.",
    "Штурм точки: мы шли героически.",
    "Нейтралы: мы просто живём. Пока.",
    "Бар «100 рентген» — как дом.",
    "Я не теряюсь. Зона меня теряет.",
    "Сначала артефакт, потом паника.",
    "Где мой болт? Он был легендарный.",
    "Сталкер без аптечки — смелый глупец.",
    "Выброс лечит лишних игроков.",
    "КПК: цель рядом. Я: где я?",
    "Провалил квест, но mood поднялся.",
    "Зона дала, Зона забрала, Зона посмеялась.",
)


MAX_QUOTE_LINES = 3
# Зафиксированная раскладка левой панели профиля (не сдвигать без явного запроса).
LOCKED_AVATAR_BOTTOM_GAP = 48
LOCKED_QUOTE_Y_OFFSET = -10
# Ориентир по длине, чтобы фраза уверенно влезала в 3 строки карточки.
MAX_QUOTE_CHAR_HINT = 130


def _quote_candidates() -> list[str]:
    short = [quote for quote in STALKER_QUOTES if len(quote) <= MAX_QUOTE_CHAR_HINT]
    return short or list(STALKER_QUOTES)


def _quote_line_step(font: ImageFont.ImageFont, zone_height: int) -> int:
    ascent, descent = font.getmetrics()
    min_step = max(ascent + descent, 14)
    if zone_height <= min_step:
        return min_step
    if MAX_QUOTE_LINES <= 1:
        return min_step
    fit_step = (zone_height - min_step) // (MAX_QUOTE_LINES - 1)
    return max(min_step, min(20, fit_step))


def _quote_block_height(lines: list[str], font: ImageFont.ImageFont, line_step: int) -> int:
    if not lines:
        return 0
    ascent, descent = font.getmetrics()
    return (len(lines) - 1) * line_step + ascent + descent

def _wrap_text_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
) -> tuple[list[str], bool]:
    if max_lines <= 0:
        return [], False
    words = text.split()
    if not words:
        return [], True
    lines: list[str] = []
    current = words[0]
    if draw.textlength(current, font=font) > max_width:
        current = _ellipsize_text(draw, current, font, max_width)
    word_idx = 1
    while word_idx < len(words):
        word = words[word_idx]
        test = f"{current} {word}"
        if draw.textlength(test, font=font) <= max_width:
            current = test
            word_idx += 1
            continue
        lines.append(current)
        if len(lines) >= max_lines:
            lines[-1] = _ellipsize_text(draw, lines[-1], font, max_width)
            return lines, False
        current = word
        if draw.textlength(current, font=font) > max_width:
            current = _ellipsize_text(draw, current, font, max_width)
        word_idx += 1
    if len(lines) < max_lines:
        lines.append(current)
    elif lines:
        lines[-1] = _ellipsize_text(draw, lines[-1], font, max_width)
        return lines[:max_lines], False
    return lines[:max_lines], word_idx >= len(words)


def _draw_stalker_quote(
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
    font: ImageFont.ImageFont,
    gap_bottom: int | None = None,
) -> None:
    padding_x = 16
    max_width = max(40, right - left - padding_x * 2)
    max_height = max(1, bottom - top)

    lines: list[str] = []
    candidates = _quote_candidates()
    for _ in range(24):
        quote = random.choice(candidates)
        candidate, complete = _wrap_text_lines(
            draw,
            quote,
            font,
            max_width,
            MAX_QUOTE_LINES,
        )
        if complete and candidate:
            lines = candidate
            break
    if not lines:
        quote = min(candidates, key=len)
        lines, _ = _wrap_text_lines(
            draw,
            quote,
            font,
            max_width,
            MAX_QUOTE_LINES,
        )
    if not lines:
        return
    line_step = _quote_line_step(font, max_height)
    text_block_h = _quote_block_height(lines, font, line_step)
    gap_end = gap_bottom if gap_bottom is not None else bottom
    quote_anchor_y = (top + gap_end) / 2 + LOCKED_QUOTE_Y_OFFSET
    start_y = int(round(quote_anchor_y - text_block_h / 2))
    start_y = max(top, min(start_y, bottom - text_block_h))
    box_width = right - left
    for idx, line in enumerate(lines):
        line_width = draw.textlength(line, font=font)
        x = left + max(padding_x, int((box_width - line_width) / 2))
        draw.text(
            (x, start_y + idx * line_step),
            line,
            fill=(232, 235, 245),
            font=font,
        )


def _draw_centered_lines_in_box(
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
    lines: list[str],
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    line_step: int | None = None,
) -> None:
    if not lines:
        return
    padding_x = 16
    max_width = max(40, right - left - padding_x * 2)
    visible = [_ellipsize_text(draw, line, font, max_width) for line in lines]
    ascent, descent = font.getmetrics()
    step = line_step or max(ascent + descent + 4, 28)
    block_h = (len(visible) - 1) * step + ascent + descent
    center_y = (top + bottom) // 2
    start_y = center_y - block_h // 2
    box_width = right - left
    for idx, line in enumerate(visible):
        line_width = draw.textlength(line, font=font)
        x = left + max(padding_x, int((box_width - line_width) / 2))
        draw.text((x, start_y + idx * step), line, fill=fill, font=font)


def _draw_power_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    value: int,
    max_value: int,
    color: tuple[int, int, int],
) -> None:
    bar_w = 240
    bar_h = 14
    draw.rounded_rectangle((x, y, x + bar_w, y + bar_h), radius=6, fill=(40, 40, 45))
    fill_w = int(bar_w * max(0.0, min(1.0, value / max_value)))
    draw.rounded_rectangle((x, y, x + fill_w, y + bar_h), radius=6, fill=color)


def _inventory_lines(character: Character) -> list[str]:
    if not character.inventory:
        return ["Пусто"]
    lines = []
    for key, amount in sorted(character.inventory.items()):
        title = ITEM_LABELS.get(key, key)
        lines.append(f"{title}: {amount}")
    return lines


def _draw_text_block(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    header: str,
    lines: list[str],
    header_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
    max_lines: int,
    max_width: int = 300,
) -> None:
    draw.text((x, y), header, fill=(218, 218, 218), font=header_font)
    draw.line((x, y + 30, x + max_width, y + 30), fill=(90, 92, 108), width=1)
    visible = lines[:max_lines]
    for i, line in enumerate(visible):
        draw.text(
            (x, y + 38 + i * 26),
            _ellipsize_text(draw, line, body_font, max_width),
            fill=(230, 230, 230),
            font=body_font,
        )


def _ellipsize_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "..."
    current = text
    while current and draw.textlength(current + suffix, font=font) > max_width:
        current = current[:-1]
    return (current + suffix) if current else suffix


def _player_name_with_medals(
    draw: ImageDraw.ImageDraw,
    nickname: str,
    medals: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    prefix = "Игрок: "
    medal_part = f" {medals.strip()}" if medals.strip() else ""
    full = prefix + nickname + medal_part
    if draw.textlength(full, font=font) <= max_width:
        return full
    medals_width = draw.textlength(medal_part, font=font)
    nick_budget = max_width - draw.textlength(prefix, font=font) - medals_width
    if nick_budget >= 24:
        return prefix + _ellipsize_text(draw, nickname, font, int(nick_budget)) + medal_part
    nick_fit = _ellipsize_text(draw, nickname, font, max(24, max_width // 3))
    rest = max_width - draw.textlength(prefix + nick_fit + " ", font=font)
    medals_fit = _ellipsize_text(draw, medals.strip(), font, max(20, int(rest)))
    return prefix + nick_fit + (f" {medals_fit}" if medals_fit else "")


def build_character_card(
    character: Character,
    *,
    rating_points: int = 0,
    storage: Storage | None = None,
) -> bytes:
    width, height = 1180, 700
    img = Image.new("RGB", (width, height), color=(21, 21, 26))
    draw = ImageDraw.Draw(img)

    title_font = _load_font(34)
    subtitle_font = _load_font(22)
    body_font = _load_font(18)
    small_font = _load_font(16)
    quote_font = _load_font(18)

    faction_color = _faction_color(character.faction)
    location_color = _location_color(character.location)
    rating = max(0, int(rating_points))
    skin = resolve_skin(rating)
    is_leader = False
    if storage is not None and character.faction:
        is_leader = storage.get_faction_leader_id(character.faction) == character.telegram_id
    rank_title = resolve_rank_title(
        faction=character.faction,
        faction_rank=character.faction_rank,
        is_leader=is_leader,
    )

    draw.rectangle((0, 0, width, 90), fill=(28, 31, 40))
    draw.text((24, 16), "Карточка персонажа", fill=(235, 235, 235), font=title_font)
    draw.text(
        (24, 56),
        f"ID-адрес: {character.player_uid}",
        fill=(208, 208, 208),
        font=small_font,
    )

    draw.rounded_rectangle((24, 108, 430, 676), radius=16, fill=(34, 36, 48), outline=(66, 68, 82), width=2)
    medal_suffix = ""
    if storage is not None:
        try:
            from app.player_medals import medals_nick_suffix

            medal_suffix = medals_nick_suffix(storage, character.telegram_id)
        except Exception:
            medal_suffix = ""
    draw.text(
        (46, 132),
        _player_name_with_medals(draw, character.nickname, medal_suffix, subtitle_font, 350),
        fill=(240, 240, 240),
        font=subtitle_font,
    )
    info_box_left = 46
    info_box_top = 164
    info_box_right = 408
    info_box_bottom = 254
    draw.rounded_rectangle(
        (info_box_left, info_box_top, info_box_right, info_box_bottom),
        radius=12,
        fill=location_color,
        outline=(210, 210, 210),
        width=2,
    )
    info_lines = [
        f"Локация: {character.location}",
        f"Группировка: {character.faction or 'не выбрана'}",
    ]
    if rank_title:
        info_lines.append(f"Звание: {rank_title}")
    _draw_centered_lines_in_box(
        draw,
        left=info_box_left,
        top=info_box_top,
        right=info_box_right,
        bottom=info_box_bottom,
        lines=info_lines,
        font=small_font,
        fill=(248, 248, 248),
    )

    panel_left = info_box_left
    panel_right = info_box_right
    panel_bottom = 676
    avatar_top = 268

    avatar = render_avatar(character, rating_points=rating, width=248, height=320)

    available_w = max(1, panel_right - panel_left)
    available_h = max(1, panel_bottom - avatar_top - 2)
    if avatar.width > available_w or avatar.height > available_h:
        scale = min(available_w / avatar.width, available_h / avatar.height)
        resized_w = max(1, int(avatar.width * scale))
        resized_h = max(1, int(avatar.height * scale))
        avatar = avatar.resize((resized_w, resized_h), Image.Resampling.LANCZOS)

    avatar_x = panel_left + (available_w - avatar.width) // 2
    avatar_y = panel_bottom - avatar.height - LOCKED_AVATAR_BOTTOM_GAP
    avatar_y = max(avatar_top, avatar_y)

    if avatar_y > info_box_bottom:
        _draw_stalker_quote(
            draw,
            left=info_box_left,
            top=info_box_bottom,
            right=info_box_right,
            bottom=avatar_y + LOCKED_QUOTE_Y_OFFSET,
            gap_bottom=avatar_y,
            font=quote_font,
        )

    if avatar.mode in {"RGBA", "LA"}:
        # Сохраняем прозрачность, чтобы не появлялся темный фон вокруг спрайта.
        img.paste(avatar, (avatar_x, avatar_y), avatar)
    else:
        img.paste(avatar, (avatar_x, avatar_y))

    # Если остаются пустые зоны сверху/снизу, подложка панели остается однотонной.

    draw.rounded_rectangle((454, 108, 1156, 676), radius=16, fill=(33, 35, 44), outline=(66, 68, 82), width=2)
    right_x = 480
    right_max_width = 650
    draw.text((right_x, 132), f"Пол: {character.gender}", fill=(220, 220, 220), font=body_font)
    draw.text(
        (right_x, 158),
        _ellipsize_text(draw, f"Группировка: {character.faction or 'не выбрана'}", body_font, right_max_width),
        fill=faction_color,
        font=body_font,
    )
    draw.text(
        (right_x, 184),
        _ellipsize_text(
            draw,
            f"Звание: {rank_title}" if rank_title else "Звание: —",
            body_font,
            right_max_width,
        ),
        fill=(225, 225, 225),
        font=body_font,
    )
    draw.text((right_x, 210), f"Баланс: {character.money} рублей", fill=(225, 225, 225), font=body_font)
    draw.text(
        (right_x, 236),
        (
            f"Транспорт: Грузовик ({max(0, min(100, int(character.truck_durability)))}%)"
            if character.truck_owned
            else (
                f"Транспорт: Нива ({max(0, min(100, int(character.niva_durability)))}%)"
                if character.niva_owned
                else (
                    "Транспорт: Велосипед"
                    if character.bicycle_owned
                    else "Транспорт: Отсутствует"
                )
            )
        ),
        fill=(225, 225, 225),
        font=body_font,
    )
    draw.text((right_x, 262), f"Дизель: {character.diesel}  Бензин: {character.gasoline}", fill=(225, 225, 225), font=body_font)
    draw.text(
        (right_x, 288),
        _ellipsize_text(draw, skin.title, body_font, right_max_width),
        fill=skin.accent_color,
        font=body_font,
    )

    draw.text((right_x, 318), "Индикаторы состояния", fill=(210, 210, 210), font=body_font)
    current_gear_power = equipment_power(character)
    bar_x = right_x + 184
    value_x = bar_x + 252

    max_hp = effective_max_health(character)
    draw.text((right_x, 344), "Здоровье", fill=(220, 220, 220), font=small_font)
    _draw_power_bar(draw, bar_x, 348, character.health, max_hp, (190, 70, 70))
    draw.text((value_x, 344), f"{character.health}/{max_hp}", fill=(220, 220, 220), font=small_font)

    draw.text((right_x, 370), "Энергия", fill=(220, 220, 220), font=small_font)
    _draw_power_bar(draw, bar_x, 374, character.energy, max(1, character.max_energy), (70, 150, 220))
    draw.text((value_x, 370), f"{character.energy}/{character.max_energy}", fill=(220, 220, 220), font=small_font)

    draw.text((right_x, 396), "Сила снаряжения", fill=(220, 220, 220), font=small_font)
    _draw_power_bar(draw, bar_x, 400, current_gear_power, 20, (170, 170, 95))
    draw.text((value_x, 396), f"{current_gear_power}/20", fill=(220, 220, 220), font=small_font)

    draw.text(
        (right_x, 424),
        f"Радиация: {character.radiation}/100   Голод: {character.hunger}/100   Жажда: {character.thirst}/100",
        fill=(208, 208, 208),
        font=small_font,
    )

    equipment = character.equipment or {}
    weapon_name = str(equipment.get("weapon", "—"))
    armor_name = str(equipment.get("armor", "—"))
    try:
        weapon_durability = int(equipment.get("weapon_durability", 100))
    except (TypeError, ValueError):
        weapon_durability = 100
    try:
        armor_durability = int(equipment.get("armor_durability", 100))
    except (TypeError, ValueError):
        armor_durability = 100
    artifact_name = str(equipment.get("artifact", "Нет"))
    defense = armor_defense(character)
    flat_mit = armor_flat_mitigation(character)
    equipment_lines = [
        f"Сила снаряжения: {equipment_power(character)}",
        f"Оружие: {weapon_name} ({weapon_durability}%)",
        f"Броня: {armor_name} ({armor_durability}%)",
    ]
    if flat_mit > 0 or defense > 0:
        bits = []
        if flat_mit > 0:
            bits.append(f"-{flat_mit}")
        if defense > 0:
            bits.append(f"апгрейд -{defense}")
        equipment_lines.append("Защита: " + ", ".join(bits))
    equipment_lines.append(f"Артефакт: {artifact_name}")
    art2 = str(equipment.get("artifact_2", "Нет") or "Нет")
    art3 = str(equipment.get("artifact_3", "Нет") or "Нет")
    if art2 not in ("", "Нет"):
        equipment_lines.append(f"Арт 2: {art2}")
    if art3 not in ("", "Нет"):
        equipment_lines.append(f"Арт 3: {art3}")
    _draw_text_block(
        draw=draw,
        x=480,
        y=456,
        header="Снаряжение",
        lines=equipment_lines,
        header_font=small_font,
        body_font=small_font,
        max_lines=6,
        max_width=300,
    )
    _draw_text_block(
        draw=draw,
        x=810,
        y=456,
        header="Инвентарь",
        lines=_inventory_lines(character),
        header_font=small_font,
        body_font=small_font,
        max_lines=4,
        max_width=320,
    )

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
