"""Лаборатории «Предел-1/2/3» — многоуровневая тактическая мини-игра.

Одна персистентная сессия на игрока (meta `lab:session:<telegram_id>`),
3 уровня внутри одной сессии + эпилог-засада на поверхности. Поле 9×9
(засада — арена 7×7), у врагов «живое» HP. Вся параметризация лабы
(ростер, записки, боссы, геометрия) живёт в LAB_DEFS — общие
тактические модули (tactical_combat / mutant_abilities / enemy_hud /
tactical_hp / tactical_turn / raid_grid) не изменяются.
"""

from __future__ import annotations

import copy
import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.artifact_hunt import _load_location_thumb, _paste_circle, _paste_rounded
from app.combat_loot import grant_combat_loot
from app.death_flavor import encounter_phrase_for_kind, killer_label_for_kind
from app.enemy_hud import EnemyHudSlot, default_hp_for_kind, draw_enemy_hud
from app.game_logic import (
    ITEM_LABELS,
    ActionResult,
    _dead_block_text,
    _is_dead,
    apply_incoming_damage,
    effective_max_health,
    h,
    is_traveling,
)
from app.mutant_abilities import (
    apply_mutant_turn_effects,
    mutant_can_melee_attack,
    mutant_extra_move_step,
    mutant_field_ability_warnings,
    mutant_can_ranged_attack,
    mutant_chase_target,
    mutant_damage_multiplier,
    mutant_extra_radiation,
    mutant_hostile_move_candidates,
    mutant_pick_move_step,
    mutant_should_move_when_chasing,
    relative_attack_side,
)
from app.mutant_assets import (
    MISSION_MUTANT_GRID_DIAMETER,
    apply_controller_aura_to_hp_map,
    mutant_grid_diameter,
)
from app.npc_assets import MISSION_NPC_GRID_DIAMETER, npc_sprite_image
from app.storage import Character, Storage
from app.tactical_combat import (
    MOVE_DELTAS,
    STALE_TURN_MESSAGE,
    apply_armor_bonus,
    best_step_toward,
    collect_player_shot_hits,
    consume_shot_ammo,
    cover_blocks_shot,
    extra_armor_from_cell,
    manhattan_distance,
    npc_weapon_damage,
    ray_cast_first_hit,
    spawn_edge_positions,
    weapon_damage,
    weapon_shoot_range,
)
from app.tactical_hp import (
    apply_tactical_medkit_spend,
    finalize_group_tactical_hp,
    plan_tactical_medkit,
)
from app.tactical_render import (
    load_tactical_font,
    paste_location_field_photo,
    paste_mutant_sprite,
    paste_npc_sprite,
    paste_player_avatar,
)

LAB_SESSION_PREFIX = "lab:session:"
LAB_ACTIVE_IDS_META = "lab:active_ids"
LAB_DONE_PREFIX = "lab:done:"
LAB_GRID_SIZE = 9
LAB_AMBUSH_GRID_SIZE = 7
LAB_ENERGY_COST = 10
LAB_TURN_SECONDS = 20
LAB_NPC_MOVE_CHANCE = 0.45
LAB_NPC_SHOOT_CHANCE = 0.6
LAB_HAZARD_DAMAGE = 110
# Базовый урон мутанта в ближнем бою — как в quest_mission для «Темной долины» (danger=3):
# base_lo = 6 + danger*4 = 18, base_hi = 12 + danger*7 = 33.
LAB_MUTANT_BASE_DAMAGE = (18, 33)

NOTE_LEVEL2_TEXT = (
    "Я, Бунзов Роман, главный учёный комплекса «Предел-1», вынужден сообщить о провале "
    "эксперимента по созданию модифицированных солдат. Люди ведут себя неуправляемо, и "
    "из-за побочных эффектов в области рта у них начали отрастать щупальца. Вынужден "
    "пройти к своим коллегам на уровень 3. Пароль, чтобы не забыть: 040826."
)
NOTE_FINAL_TEXT = (
    "Артефакт готов. Один из трёх готов! Осталось дождаться коллег с «Предел-2» и "
    "«Предел-3» — и тогда мы соберём самый сильный артефакт в Зоне."
)
# Свои записки у «Предел-2» и «Предел-3» (жалоба игроков: «записка одна и та же»).
NOTE_LIMIT2_LEVEL2_TEXT = (
    "Я, Ветрова Алла, ведущий инженер комплекса «Предел-2». Пси-щит на втором уровне "
    "не выдержал: техперсонал третьей смены оглох от гула излучателя и ушёл в "
    "зомбированные. Теперь они качаются у колб и не узнают своих. Опытный образец "
    "увезли в бункер на уровень 3. Код шлюза, чтобы не забыть: 112358."
)
NOTE_LIMIT2_FINAL_TEXT = (
    "Второй фрагмент готов — пси-ядро держит контур. Осталось дождаться коллег с "
    "«Предел-1» и «Предел-3» — и тогда мы соберём самый сильный артефакт в Зоне."
)
NOTE_LIMIT3_LEVEL2_TEXT = (
    "Я, Гордеев Матвей, куратор комплекса «Предел-3». Из зомбированных у антенного "
    "поля вывели бюреров: телекинез вместо рук и полное послушание. Один из них теперь "
    "держит контур сборки на уровне 3. Пароль лаборатории, чтобы не забыть: 090964."
)
NOTE_LIMIT3_FINAL_TEXT = (
    "Все три фрагмента собраны! Сердце — из «Предел-1», пси-ядро — из «Предел-2», "
    "бронированная оболочка — отсюда. В контуре сборки они сливаются в один артефакт, "
    "самый сильный артефакт в Зоне. Выноси его на поверхность — и берегись засады: "
    "за ним уже охотятся."
)
AMBUSH_INTRO_TEXT = (
    "При выходе на поверхность тебя встречает засада — 6 Хай сталкеров в хорошей "
    "броне и с оружием."
)

# Общие вступления (Предел-1); у Предел-2/3 свои — LEVEL_INTROS_LIMIT2/3 в LAB_DEFS.
LEVEL_INTROS: dict[int, str] = {
    2: (
        "Уровень 2 — большой зал «колб». Среди пустых капсул прячутся кровососы: "
        "их цепкие твари, но и они смертны. Дверь дальше открыта — нужно просто зачистить зал."
    ),
    3: (
        "Уровень 3 — сердце комплекса. Здесь держали образец: Псевдогигант, "
        "«продукт» экспериментов Бунзова. Убей его — дверь к награде откроется сама."
    ),
}
LEVEL_INTROS_LIMIT2: dict[int, str] = {
    2: (
        "Уровень 2 — зал пси-колб. У колб качаются зомбированные технари, в тени рыщут "
        "кровососы. Дверь дальше открыта — нужно просто зачистить зал."
    ),
    3: (
        "Уровень 3 — бункер образца: Псевдогигант на пси-цепи, рядом псевдособаки-"
        "охранники. Убей образец — дверь к награде откроется сама."
    ),
}
LEVEL_INTROS_LIMIT3: dict[int, str] = {
    2: (
        "Уровень 2 — зал антенных колб. Контролёр давит на разум из глубины зала, "
        "зомбированные идут на шум, кровососы заходят с флангов. Зачисти зал — и держись "
        "подальше от контролёра."
    ),
    3: (
        "Уровень 3 — сердце комплекса: контур сборки держит бюрер. Телекинез бьёт по "
        "прямой, зомбированные прикрывают его. Убей бюрера — дверь к награде откроется сама."
    ),
}

# Атака со спины/боков — подписи для лога (как в quest_mission).
ATTACK_SIDE_LABEL = {
    "front": "⬆️ в лоб",
    "back": "⬇️ со спины",
    "left": "⬅️ слева",
    "right": "➡️ справа",
}

MUTANT_KIND_LABELS: dict[str, str] = {
    "bloodsucker": "Кровосос",
    "giant": "Псевдогигант",
    "blind_dog": "Слепой пёс",
    "pseudodog": "Псевдособака",
    "tushkano": "Тушкан",
    "flesh": "Плоть",
    "controller": "Контролёр",
    "burer": "Бюрер",
    "zombie": "Зомбированный",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _deadline_iso(seconds: int) -> str:
    return (_utc_now() + timedelta(seconds=seconds)).isoformat()


def _parse_deadline(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# LAB_DEFS — вся параметризация «Предел-1» данными.
# ---------------------------------------------------------------------------

def _corridor() -> list[tuple[int, int]]:
    return [(4, y) for y in range(1, 8)]


def _room(x0: int, x1: int, y0: int, y1: int) -> list[tuple[int, int]]:
    return [(x, y) for x in range(x0, x1) for y in range(y0, y1)]


LAB_DEFS: dict[str, dict[str, Any]] = {
    "limit1": {
        "lab_id": "limit1",
        "label": "Предел-1",
        "location": "НИИ Агропром",
        "note_author": "Бунзова",
        "note_level2_text": NOTE_LEVEL2_TEXT,
        "note_final_text": NOTE_FINAL_TEXT,
        "grid": LAB_GRID_SIZE,
        "levels": {
            # --- Уровень 1: коридор + комнаты, бойцы Монолита с ключ-картой ---
            1: {
                "spawn": (4, 8),
                "exit_door": (4, 0),
                "door_lock": {"type": "key_item", "item": "keycard"},
                "rooms": {
                    "left_loot": "loot",
                    "right_loot": "loot",
                    "left_hostile": "hostile",
                    "right_empty": "cleared",
                },
                "room_cells": {
                    "left_loot": _room(1, 4, 1, 4),
                    "right_loot": _room(5, 8, 1, 4),
                    "left_hostile": _room(1, 4, 5, 8),
                    "right_empty": _room(5, 8, 5, 8),
                },
                "walkable": (
                    _room(1, 4, 1, 4)
                    + _room(5, 8, 1, 4)
                    + _room(1, 4, 5, 8)
                    + _room(5, 8, 5, 8)
                    + _corridor()
                    + [(4, 8), (4, 0)]
                ),
                "loot_cells": {
                    "2,2": "medkit",
                    "1,3": "ammo_pack",
                    "3,3": "ammo_rifle",
                    "6,2": "medkit",
                    "5,3": "ammo_pack",
                    "7,3": "ammo_pistol",
                },
                "note_cells": {},
                "cover": [(2, 5), (2, 7)],
                # Жёлтые метки растяжек убраны по жалобе игроков («иконки не к месту»):
                # на поле остаются только зелёный лут и синяя рамка двери.
                "fog": True,
                "enemies": [
                    {
                        "pos": (1, 6),
                        "type": "npc",
                        "kind": "monolith",
                        "weapon": "АК-74",
                        "armor": "Экзоскелет Монолита",
                        "hp": 35,
                        "damage_mult": 1.0,
                    },
                    {
                        "pos": (3, 6),
                        "type": "npc",
                        "kind": "monolith",
                        "weapon": "АК-74",
                        "armor": "Экзоскелет Монолита",
                        "hp": 35,
                        "damage_mult": 1.0,
                    },
                ],
            },
            # --- Уровень 2: коридор + большой зал «колб» с кровососами ---
            2: {
                "spawn": (4, 8),
                "exit_door": (4, 0),
                "door_lock": {"type": "none"},
                "rooms": {"colony_hall": "hostile"},
                "room_cells": {"colony_hall": _room(1, 8, 2, 7)},
                "walkable": (_room(1, 8, 2, 7) + _corridor() + [(4, 8), (4, 0)]),
                "loot_cells": {
                    "1,5": "medkit",
                    "2,5": "ammo_rifle",
                    "7,5": "ammo_pack",
                },
                "note_cells": {"4,4": "note_level2"},
                "cover": [],
                "enemies": [
                    # hp = round(28 * 1.8) = 50 относительно обычного кровососа (28).
                    {
                        "pos": (2, 3),
                        "type": "mutant",
                        "kind": "bloodsucker",
                        "hp_mult": 1.8,
                        "hp": 50,
                        "damage_mult": 1.6,
                    },
                    {
                        "pos": (6, 3),
                        "type": "mutant",
                        "kind": "bloodsucker",
                        "hp_mult": 1.8,
                        "hp": 50,
                        "damage_mult": 1.6,
                    },
                    {
                        "pos": (4, 5),
                        "type": "mutant",
                        "kind": "bloodsucker",
                        "hp_mult": 1.8,
                        "hp": 50,
                        "damage_mult": 1.6,
                    },
                ],
            },
            # --- Уровень 3: босс — Псевдогигант, за ним комната с наградой ---
            3: {
                "spawn": (4, 8),
                "exit_door": (4, 0),
                "door_lock": {"type": "boss_kill"},
                "rooms": {"boss_hall": "hostile"},
                "room_cells": {"boss_hall": _room(1, 8, 1, 5)},
                "walkable": (_room(1, 8, 1, 5) + [(4, y) for y in range(4, 9)] + [(4, 0)]),
                "loot_cells": {
                    "1,3": "medkit",
                    "7,3": "ammo_pack",
                    "4,7": "ammo_rifle",
                },
                "note_cells": {},
                "cover": [],
                "enemies": [
                    # hp = round(100 * 2.5) = 250 относительно обычного гиганта (100).
                    {
                        "pos": (4, 2),
                        "type": "mutant",
                        "kind": "giant",
                        "hp_mult": 2.5,
                        "hp": 250,
                        "damage_mult": 1.8,
                    },
                    # Прислужники образца — слепые псы в углах зала.
                    {
                        "pos": (2, 4),
                        "type": "mutant",
                        "kind": "blind_dog",
                        "hp": 12,
                        "damage_mult": 0.8,
                    },
                    {
                        "pos": (7, 4),
                        "type": "mutant",
                        "kind": "blind_dog",
                        "hp": 12,
                        "damage_mult": 0.8,
                    },
                ],
            },
        },
        "ambush": {
            "grid": LAB_AMBUSH_GRID_SIZE,
            "spawn": (3, 6),
            "intro": AMBUSH_INTRO_TEXT,
            "count": 6,
            "weapons": ("СВДм-2", "Гаусс-пушка"),
            "armors": ("Костюм СЕВА", "Научный костюм"),
            "enemy_build": {
                "type": "npc",
                "kind": "dark_stalker",
                "hp": 40,
                "damage_mult": 1.4,
            },
        },
    },
}


def _lab_variant(
    lab_id: str,
    label: str,
    location: str,
    *,
    guard_weapon: str = "АК-74",
    guard_hp: int = 35,
    level1_extra: list[dict[str, Any]] | None = None,
    level2_enemies: list[dict[str, Any]] | None = None,
    boss_kind: str = "giant",
    boss_hp: int = 250,
    boss_escort: list[dict[str, Any]] | None = None,
    ambush_weapons: tuple[str, str] = ("СВДм-2", "Гаусс-пушка"),
    note_author: str = "Бунзова",
    note_level2_text: str = NOTE_LEVEL2_TEXT,
    note_final_text: str = NOTE_FINAL_TEXT,
    level_intros: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Вариант «Предел-1» со своим именем, локацией, ростером и записками.

    Охрана у всех лаб — бойцы Монолита (kind 'monolith' — иконка/лейбл из
    npc_assets), меняются оружие, HP и состав. Засада на поверхности
    везде одна — хай-сталкеры (kind 'dark_stalker'), отличаются только
    стволы.
    """
    cfg = copy.deepcopy(LAB_DEFS["limit1"])
    cfg["lab_id"] = lab_id
    cfg["label"] = label
    cfg["location"] = location
    for enemy in cfg["levels"][1]["enemies"]:
        enemy["weapon"] = guard_weapon
        enemy["hp"] = guard_hp
    if level1_extra:
        cfg["levels"][1]["enemies"].extend(copy.deepcopy(level1_extra))
    if level2_enemies is not None:
        cfg["levels"][2]["enemies"] = copy.deepcopy(level2_enemies)
    boss = cfg["levels"][3]["enemies"][0]
    boss["kind"] = boss_kind
    boss["hp"] = boss_hp
    if boss_escort is not None:
        cfg["levels"][3]["enemies"] = [boss] + copy.deepcopy(boss_escort)
    cfg["ambush"]["weapons"] = list(ambush_weapons)
    cfg["note_author"] = note_author
    cfg["note_level2_text"] = note_level2_text
    cfg["note_final_text"] = note_final_text
    if level_intros:
        cfg["level_intros"] = dict(level_intros)
    return cfg


# Предел-2 (Янтарь): пси-лаборатория — зомбированный техперсонал + кровососы,
# охрана Монолита усилена наёмником СБ, у босса псевдособаки вместо псов.
LAB_DEFS["limit2"] = _lab_variant(
    "limit2",
    "Предел-2",
    "Янтарь",
    guard_weapon="АКС-74У",
    guard_hp=40,
    level1_extra=[
        {
            "pos": (2, 5),
            "type": "npc",
            "kind": "mercenary",
            "weapon": "СПАС-12",
            "armor": "Бронежилет СБ комплекса",
            "hp": 30,
            "damage_mult": 1.1,
        }
    ],
    level2_enemies=[
        # Кровососы из экспериментов Ветровой (hp = round(28 * 1.8) = 50 → 60 с усилением).
        {"pos": (2, 3), "type": "mutant", "kind": "bloodsucker", "hp_mult": 1.8, "hp": 60, "damage_mult": 1.6},
        {"pos": (6, 3), "type": "mutant", "kind": "bloodsucker", "hp_mult": 1.8, "hp": 60, "damage_mult": 1.6},
        # Зомбированный техперсонал из записки — идут в лоб и накладывают рад.
        {"pos": (3, 5), "type": "mutant", "kind": "zombie", "hp": 24, "damage_mult": 1.0},
        {"pos": (5, 5), "type": "mutant", "kind": "zombie", "hp": 24, "damage_mult": 1.0},
    ],
    boss_hp=300,
    boss_escort=[
        {"pos": (2, 4), "type": "mutant", "kind": "pseudodog", "hp": 24, "damage_mult": 0.9},
        {"pos": (7, 4), "type": "mutant", "kind": "pseudodog", "hp": 24, "damage_mult": 0.9},
    ],
    ambush_weapons=("Винтарь ВС", "Гаусс-пушка"),
    note_author="Ветровой",
    note_level2_text=NOTE_LIMIT2_LEVEL2_TEXT,
    note_final_text=NOTE_LIMIT2_FINAL_TEXT,
    level_intros=LEVEL_INTROS_LIMIT2,
)

# Предел-3 (Радар): самый жёсткий — контролёр давит разум (аура + сбой взгляда),
# зомбированные идут в лоб, босс-бюрер держит контур сборки. Охрана — Монолит с гауссами.
LAB_DEFS["limit3"] = _lab_variant(
    "limit3",
    "Предел-3",
    "Радар",
    guard_weapon="Гаусс-пушка",
    guard_hp=45,
    level1_extra=[
        {
            "pos": (2, 7),
            "type": "npc",
            "kind": "monolith",
            "weapon": "Гаусс-пушка",
            "armor": "Экзоскелет Монолита",
            "hp": 35,
            "damage_mult": 1.15,
        }
    ],
    level2_enemies=[
        # Контролёр не атакует сам, но каждый ход давит разум (−HP, сбой взгляда).
        {"pos": (4, 2), "type": "mutant", "kind": "controller", "hp": 36, "damage_mult": 1.0},
        {"pos": (2, 3), "type": "mutant", "kind": "bloodsucker", "hp_mult": 1.8, "hp": 70, "damage_mult": 1.6},
        {"pos": (6, 3), "type": "mutant", "kind": "bloodsucker", "hp_mult": 1.8, "hp": 70, "damage_mult": 1.6},
        {"pos": (3, 5), "type": "mutant", "kind": "zombie", "hp": 26, "damage_mult": 1.1},
        {"pos": (5, 5), "type": "mutant", "kind": "zombie", "hp": 26, "damage_mult": 1.1},
    ],
    boss_kind="burer",
    boss_hp=350,
    boss_escort=[
        {"pos": (2, 4), "type": "mutant", "kind": "zombie", "hp": 26, "damage_mult": 0.9},
        {"pos": (7, 4), "type": "mutant", "kind": "zombie", "hp": 26, "damage_mult": 0.9},
    ],
    ambush_weapons=("ВСС «Серебряный сталкер»", "Гаусс-пушка"),
    note_author="Гордеева",
    note_level2_text=NOTE_LIMIT3_LEVEL2_TEXT,
    note_final_text=NOTE_LIMIT3_FINAL_TEXT,
    level_intros=LEVEL_INTROS_LIMIT3,
)


def _lab_def(lab_id: str = "limit1") -> dict[str, Any]:
    return LAB_DEFS[lab_id]


def _lab_level_cfg(lab_id: str, level: int) -> dict[str, Any]:
    return LAB_DEFS[lab_id]["levels"][int(level)]


def _ambush_cfg(lab_id: str = "limit1") -> dict[str, Any]:
    return LAB_DEFS[lab_id]["ambush"]


def _level_intro(lab_id: str, level: int) -> str:
    """Вступление к уровню: у каждой лабы свой текст, иначе общий LEVEL_INTROS."""
    lab_cfg = LAB_DEFS.get(str(lab_id)) or {}
    intro = (lab_cfg.get("level_intros") or {}).get(int(level))
    return str(intro or LEVEL_INTROS.get(int(level), ""))


# ---------------------------------------------------------------------------
# LabSession — dataclass, raid-совместимые поля + свойства для способностей мутантов.
# ---------------------------------------------------------------------------

@dataclass
class LabSession:
    session_id: str
    telegram_id: int
    lab_id: str = "limit1"
    label: str = "Предел-1"
    location: str = "Темная долина"
    player_ids: list[int] = field(default_factory=list)
    level: int = 1
    # "combat" | "transition" | "ambush_choice" | "ambush_combat" | "done"
    stage: str = "combat"
    finished: bool = False
    success: bool = False
    started_at: str | None = None
    turn_deadline: str | None = None
    turn_seq: int = 0
    positions: dict[str, list[int]] = field(default_factory=dict)
    hp: dict[str, int] = field(default_factory=dict)
    death_causes: dict[str, str] = field(default_factory=dict)
    death_killers: dict[str, str] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    player_facing: str = "down"
    enemies: list[list[int]] = field(default_factory=list)
    enemy_types: list[str] = field(default_factory=list)
    enemy_kinds: list[str] = field(default_factory=list)
    enemy_weapons: list[str | None] = field(default_factory=list)
    enemy_hp: list[int] = field(default_factory=list)
    enemy_max_hp: list[int] = field(default_factory=list)
    enemy_damage_mults: list[float] = field(default_factory=list)
    cover: list[list[int]] = field(default_factory=list)
    grid: int = LAB_GRID_SIZE
    rooms: dict[str, str] = field(default_factory=dict)
    collected: dict[str, bool] = field(default_factory=dict)
    opened_doors: list[str] = field(default_factory=list)
    boss_killed: bool = False
    ambush_state: str = "pending"
    message_ids: dict[str, int] = field(default_factory=dict)

    @property
    def player(self) -> tuple[int, int]:
        return self.pos(self.telegram_id)

    def pos(self, player_id: int) -> tuple[int, int]:
        raw = self.positions.get(str(player_id), [0, 0])
        return int(raw[0]), int(raw[1])

    def set_pos(self, player_id: int, pos: tuple[int, int]) -> None:
        self.positions[str(player_id)] = [int(pos[0]), int(pos[1])]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "telegram_id": int(self.telegram_id),
            "lab_id": self.lab_id,
            "label": self.label,
            "location": self.location,
            "player_ids": [int(x) for x in self.player_ids],
            "level": int(self.level),
            "stage": self.stage,
            "finished": bool(self.finished),
            "success": bool(self.success),
            "started_at": self.started_at,
            "turn_deadline": self.turn_deadline,
            "turn_seq": int(self.turn_seq),
            "positions": {str(k): [int(v[0]), int(v[1])] for k, v in self.positions.items()},
            "hp": {str(k): int(v) for k, v in self.hp.items()},
            "death_causes": dict(self.death_causes),
            "death_killers": dict(self.death_killers),
            "log": list(self.log),
            "player_facing": self.player_facing,
            "enemies": [list(p) for p in self.enemies],
            "enemy_types": list(self.enemy_types),
            "enemy_kinds": list(self.enemy_kinds),
            "enemy_weapons": list(self.enemy_weapons),
            "enemy_hp": [int(x) for x in self.enemy_hp],
            "enemy_max_hp": [int(x) for x in self.enemy_max_hp],
            "enemy_damage_mults": [float(x) for x in self.enemy_damage_mults],
            "cover": [list(p) for p in self.cover],
            "grid": int(self.grid),
            "rooms": dict(self.rooms),
            "collected": {str(k): bool(v) for k, v in self.collected.items()},
            "opened_doors": list(self.opened_doors),
            "boss_killed": bool(self.boss_killed),
            "ambush_state": self.ambush_state,
            "message_ids": {str(k): int(v) for k, v in self.message_ids.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LabSession:
        session = cls(
            session_id=str(raw.get("session_id") or ""),
            telegram_id=int(raw.get("telegram_id") or 0),
            lab_id=str(raw.get("lab_id") or "limit1"),
            label=str(raw.get("label") or "Предел-1"),
            location=str(raw.get("location") or "Темная долина"),
            player_ids=[int(x) for x in (raw.get("player_ids") or [])],
            level=int(raw.get("level") or 1),
            stage=str(raw.get("stage") or "combat"),
            finished=bool(raw.get("finished")),
            success=bool(raw.get("success")),
            started_at=raw.get("started_at"),
            turn_deadline=raw.get("turn_deadline"),
            turn_seq=int(raw.get("turn_seq") or 0),
            positions={str(k): [int(v[0]), int(v[1])] for k, v in (raw.get("positions") or {}).items()},
            hp={str(k): int(v) for k, v in (raw.get("hp") or {}).items()},
            death_causes={str(k): str(v) for k, v in (raw.get("death_causes") or {}).items()},
            death_killers={str(k): str(v) for k, v in (raw.get("death_killers") or {}).items()},
            log=[str(x) for x in (raw.get("log") or [])],
            player_facing=str(raw.get("player_facing") or "down"),
            enemies=[[int(p[0]), int(p[1])] for p in (raw.get("enemies") or [])],
            enemy_types=[str(x) for x in (raw.get("enemy_types") or [])],
            enemy_kinds=[str(x) for x in (raw.get("enemy_kinds") or [])],
            enemy_weapons=[(str(x) if x else None) for x in (raw.get("enemy_weapons") or [])],
            enemy_hp=[int(x) for x in (raw.get("enemy_hp") or [])],
            enemy_max_hp=[int(x) for x in (raw.get("enemy_max_hp") or [])],
            enemy_damage_mults=[float(x) for x in (raw.get("enemy_damage_mults") or [])],
            cover=[[int(p[0]), int(p[1])] for p in (raw.get("cover") or [])],
            grid=int(raw.get("grid") or LAB_GRID_SIZE),
            rooms={str(k): str(v) for k, v in (raw.get("rooms") or {}).items()},
            collected={str(k): bool(v) for k, v in (raw.get("collected") or {}).items()},
            opened_doors=[str(x) for x in (raw.get("opened_doors") or [])],
            boss_killed=bool(raw.get("boss_killed")),
            ambush_state=str(raw.get("ambush_state") or "pending"),
            message_ids={str(k): int(v) for k, v in (raw.get("message_ids") or {}).items()},
        )
        if not session.player_ids and session.telegram_id:
            session.player_ids = [session.telegram_id]
        return session


# ---------------------------------------------------------------------------
# Хранение: meta JSON + реестр активных.
# ---------------------------------------------------------------------------

def _session_key(telegram_id: int) -> str:
    return f"{LAB_SESSION_PREFIX}{int(telegram_id)}"


def _done_key(lab_id: str, telegram_id: int) -> str:
    return f"{LAB_DONE_PREFIX}{lab_id}:{int(telegram_id)}"


def get_lab_session(storage: Storage, telegram_id: int) -> LabSession | None:
    raw = storage.get_meta(_session_key(telegram_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return LabSession.from_dict(data)
    except Exception:
        storage.delete_meta(_session_key(telegram_id))
        return None


def save_lab_session(storage: Storage, session: LabSession) -> None:
    storage.set_meta(_session_key(session.telegram_id), json.dumps(session.to_dict(), ensure_ascii=False))
    _register_active(storage, session.telegram_id)


def clear_lab_session(storage: Storage, telegram_id: int) -> None:
    storage.delete_meta(_session_key(telegram_id))
    _unregister_active(storage, telegram_id)


def _register_active(storage: Storage, telegram_id: int) -> None:
    raw = storage.get_meta(LAB_ACTIVE_IDS_META)
    ids: list[int] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                ids = [int(x) for x in parsed]
        except json.JSONDecodeError:
            ids = []
    tid = int(telegram_id)
    if tid not in ids:
        ids.append(tid)
    storage.set_meta(LAB_ACTIVE_IDS_META, json.dumps(ids, ensure_ascii=False))


def _unregister_active(storage: Storage, telegram_id: int) -> None:
    raw = storage.get_meta(LAB_ACTIVE_IDS_META)
    if not raw:
        return
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return
        ids = [int(x) for x in parsed if int(x) != int(telegram_id)]
    except json.JSONDecodeError:
        return
    if ids:
        storage.set_meta(LAB_ACTIVE_IDS_META, json.dumps(ids, ensure_ascii=False))
    else:
        storage.delete_meta(LAB_ACTIVE_IDS_META)


def _mark_lab_done(storage: Storage, lab_id: str, telegram_id: int) -> None:
    storage.set_meta(_done_key(lab_id, telegram_id), "1")


def _save_turn(storage: Storage, session: LabSession, expected_seq: int) -> bool:
    from app.tactical_turn import save_turn_if_seq_ok

    return save_turn_if_seq_ok(
        storage,
        meta_key=_session_key(session.telegram_id),
        session=session,
        from_dict=LabSession.from_dict,
        save_fn=lambda st, sess: save_lab_session(st, sess),
        expected_seq=expected_seq,
    )


def _advance(session: LabSession) -> None:
    session.turn_seq += 1
    session.turn_deadline = _deadline_iso(LAB_TURN_SECONDS)


# ---------------------------------------------------------------------------
# Построение уровней / засады.
# ---------------------------------------------------------------------------

def _reset_enemies(session: LabSession) -> None:
    session.enemies = []
    session.enemy_types = []
    session.enemy_kinds = []
    session.enemy_weapons = []
    session.enemy_hp = []
    session.enemy_max_hp = []
    session.enemy_damage_mults = []


def _load_level(session: LabSession, telegram_id: int, level: int, *, new_floor: bool = True) -> None:
    """Пересобрать поле под уровень level (вызов из старта/переходов)."""
    cfg = _lab_level_cfg(session.lab_id, level)
    session.level = int(level)
    session.grid = int(cfg.get("grid") or LAB_GRID_SIZE)
    session.stage = "transition" if new_floor else "combat"
    spawn = tuple(cfg.get("spawn") or (4, 8))
    session.positions = {str(telegram_id): [int(spawn[0]), int(spawn[1])]}
    session.player_facing = "down"
    _reset_enemies(session)
    session.cover = [list(c) for c in (cfg.get("cover") or [])]
    session.rooms = dict(cfg.get("rooms") or {})
    for edef in cfg.get("enemies") or []:
        pos = tuple(edef["pos"])
        session.enemies.append([int(pos[0]), int(pos[1])])
        session.enemy_types.append(str(edef.get("type") or "mutant"))
        session.enemy_kinds.append(str(edef.get("kind") or "blind_dog"))
        session.enemy_weapons.append(str(edef["weapon"]) if edef.get("weapon") else None)
        hp = int(edef.get("hp") or 0) or default_hp_for_kind(str(edef.get("kind") or "blind_dog"))
        session.enemy_hp.append(hp)
        session.enemy_max_hp.append(hp)
        session.enemy_damage_mults.append(float(edef.get("damage_mult") or 1.0))
    session.log = []


def _spawn_ambush(session: LabSession, telegram_id: int) -> None:
    cfg = _ambush_cfg(session.lab_id)
    session.grid = int(cfg.get("grid") or LAB_AMBUSH_GRID_SIZE)
    session.stage = "ambush_combat"
    session.ambush_state = "fought"
    spawn = tuple(cfg.get("spawn") or (3, 6))
    session.positions = {str(telegram_id): [int(spawn[0]), int(spawn[1])]}
    session.player_facing = "down"
    _reset_enemies(session)
    session.cover = []
    session.rooms = {}
    build = dict(cfg.get("enemy_build") or {})
    weapons = tuple(cfg.get("weapons") or ("СВДм-2", "Гаусс-пушка"))
    count = int(cfg.get("count") or 6)
    forbidden: set[tuple[int, int]] = {spawn}
    cells = spawn_edge_positions(session.grid, count, forbidden)
    for i, cell in enumerate(cells):
        session.enemies.append([int(cell[0]), int(cell[1])])
        session.enemy_types.append(str(build.get("type") or "npc"))
        session.enemy_kinds.append(str(build.get("kind") or "dark_stalker"))
        session.enemy_weapons.append(weapons[i % len(weapons)])
        hp = int(build.get("hp") or 40)
        session.enemy_hp.append(hp)
        session.enemy_max_hp.append(hp)
        session.enemy_damage_mults.append(float(build.get("damage_mult") or 1.4))
    session.log = []


# ---------------------------------------------------------------------------
# Утилиты поля.
# ---------------------------------------------------------------------------

def _cover_set(session: LabSession) -> set[tuple[int, int]]:
    return {(int(c[0]), int(c[1])) for c in session.cover}


def _enemy_cells(session: LabSession) -> set[tuple[int, int]]:
    return {(int(e[0]), int(e[1])) for e in session.enemies}


def _level_walkable(session: LabSession) -> set[tuple[int, int]]:
    if session.stage == "ambush_combat":
        return set()
    cfg = _lab_level_cfg(session.lab_id, session.level)
    return {(int(c[0]), int(c[1])) for c in (cfg.get("walkable") or [])}


def _is_walkable(session: LabSession, cell: tuple[int, int]) -> bool:
    if session.stage == "ambush_combat":
        return 0 <= cell[0] < session.grid and 0 <= cell[1] < session.grid
    return cell in _level_walkable(session)


def _enemy_mult(session: LabSession, index: int) -> float:
    if index < len(session.enemy_damage_mults):
        return float(session.enemy_damage_mults[index])
    return 1.0


def _enemy_index_at(session: LabSession, cell: tuple[int, int]) -> int | None:
    for i, pos in enumerate(session.enemies):
        if int(pos[0]) == cell[0] and int(pos[1]) == cell[1]:
            return i
    return None


def _enemy_label(kind: str, *, npc: bool) -> str:
    if npc:
        return killer_label_for_kind(kind, npc=True)
    return MUTANT_KIND_LABELS.get(kind, killer_label_for_kind(kind, npc=False))


def _remove_enemy_at_index(session: LabSession, idx: int) -> str:
    kind = session.enemy_kinds[idx] if idx < len(session.enemy_kinds) else "mutant"
    etype = session.enemy_types[idx] if idx < len(session.enemy_types) else "mutant"
    session.enemies.pop(idx)
    if idx < len(session.enemy_types):
        session.enemy_types.pop(idx)
    if idx < len(session.enemy_kinds):
        session.enemy_kinds.pop(idx)
    if idx < len(session.enemy_weapons):
        session.enemy_weapons.pop(idx)
    if idx < len(session.enemy_hp):
        session.enemy_hp.pop(idx)
    if idx < len(session.enemy_max_hp):
        session.enemy_max_hp.pop(idx)
    if idx < len(session.enemy_damage_mults):
        session.enemy_damage_mults.pop(idx)
    # Босс третьего уровня: победа → дверь открывается сама (Предел-3: бюрер).
    if kind in ("giant", "burer"):
        session.boss_killed = True
    return _enemy_label(kind, npc=etype == "npc")


def _update_triggers(storage: Storage, session: LabSession) -> None:
    """Флаги прогресса по зачистке (ключ-карта уровня 1)."""
    if session.stage != "combat":
        return
    if session.level == 1 and not session.enemies and not session.collected.get("keycard"):
        session.collected["keycard"] = True
        session.rooms["left_hostile"] = "cleared"
        session.log.append("🗝 Ты нашёл ключ-карту у усиленных бойцов Монолита.")
    if session.level == 3 and session.boss_killed and not session.collected.get("boss_cleared"):
        session.collected["boss_cleared"] = True
        session.rooms["boss_hall"] = "cleared"
        session.log.append("👹 Образец уничтожен — дверь к награде открылась.")


def _apply_damage_to_player(
    session: LabSession,
    dmg: int,
    *,
    cause: str,
    killer_name: str,
) -> None:
    key = str(session.telegram_id)
    new_hp = max(0, session.hp.get(key, 0) - dmg)
    session.hp[key] = new_hp
    if new_hp <= 0 and not session.death_killers.get(key):
        session.death_causes[key] = cause
        session.death_killers[key] = killer_name


# ---------------------------------------------------------------------------
# Урон: лаб-слой поверх существующих функций (tactical_combat / mutant_abilities).
# ---------------------------------------------------------------------------

def _mutant_attack_damage(
    session: LabSession,
    character: Character | None,
    *,
    kind: str,
    pos: tuple[int, int],
    mult: float,
    ranged: bool = False,
) -> tuple[int, str]:
    """Урон мутанта: база → mutant_damage_multiplier → apply_incoming_damage → × damage_mult."""
    side = relative_attack_side(session.player_facing, session.player, pos)
    base_lo, base_hi = LAB_MUTANT_BASE_DAMAGE
    raw = random.randint(base_lo, base_hi)
    raw = max(1, int(raw * mutant_damage_multiplier(kind, side, session.enemy_kinds)))
    if ranged:
        raw = max(1, int(raw * 0.72))
    dmg = apply_incoming_damage(raw, character, min_damage=1) if character is not None else raw
    dmg = max(20, int(round(dmg * mult)))
    return dmg, side


def _mutant_attack(
    storage: Storage,
    session: LabSession,
    character: Character | None,
    *,
    kind: str,
    pos: tuple[int, int],
    mult: float,
    ranged: bool = False,
) -> str:
    tid = session.telegram_id
    dmg, side = _mutant_attack_damage(
        session,
        character,
        kind=kind,
        pos=pos,
        mult=mult,
        ranged=ranged,
    )
    rad = mutant_extra_radiation(kind, side)
    if rad > 0:
        try:
            storage.adjust_survival(tid, radiation_delta=rad)
        except Exception:
            rad = 0
    _apply_damage_to_player(session, dmg, cause="mutant", killer_name=_enemy_label(kind, npc=False))
    side_txt = ATTACK_SIDE_LABEL.get(side, side)
    rad_txt = f" ☢+{rad}" if rad else ""
    phrase = encounter_phrase_for_kind(kind, npc=False)
    ranged_txt = "Удар" if ranged else "Бой"
    return f"{ranged_txt} {phrase} ({side_txt}): −{dmg} HP.{rad_txt}"


def _npc_melee_attack(
    session: LabSession,
    character: Character | None,
    *,
    kind: str,
    weapon: str | None,
    mult: float,
) -> str:
    raw = npc_weapon_damage(weapon or "ПМ")
    dmg = apply_incoming_damage(raw, character, min_damage=1) if character is not None else raw
    dmg = max(20, int(round(dmg * mult)))
    _apply_damage_to_player(session, dmg, cause="npc", killer_name=_enemy_label(kind, npc=True))
    phrase = encounter_phrase_for_kind(kind, npc=True)
    return f"Бой {phrase}: −{dmg} HP."


def _direction_to(origin: tuple[int, int], target: tuple[int, int]) -> str | None:
    ox, oy = origin
    tx, ty = target
    if ox == tx:
        return "down" if ty > oy else "up"
    if oy == ty:
        return "right" if tx > ox else "left"
    return None


def _npc_shots(storage: Storage, session: LabSession) -> list[str]:
    """Огонь НПС: npc_weapon_damage → apply_incoming_damage → × damage_mult лабы."""
    notes: list[str] = []
    tid = session.telegram_id
    player_pos = session.pos(tid)
    cover = _cover_set(session)
    character = storage.get_character(tid, refresh_energy=False)
    for i, pos_raw in enumerate(session.enemies):
        if i >= len(session.enemy_types) or session.enemy_types[i] != "npc":
            continue
        if random.random() > LAB_NPC_SHOOT_CHANCE:
            continue
        pos = (int(pos_raw[0]), int(pos_raw[1]))
        weapon = session.enemy_weapons[i] if i < len(session.enemy_weapons) else "ПМ"
        weapon = weapon or "ПМ"
        direction = _direction_to(pos, player_pos)
        if direction is None:
            continue
        hit_cell, _kind = ray_cast_first_hit(
            pos,
            direction,
            grid=session.grid,
            max_range=weapon_shoot_range(weapon),
            blockers=cover,
            targets={player_pos: str(tid)},
        )
        if hit_cell is None or hit_cell != player_pos:
            continue
        if cover_blocks_shot(player_pos, cover):
            continue
        raw = npc_weapon_damage(weapon)
        extra = extra_armor_from_cell(player_pos, set())
        pre = apply_armor_bonus(raw, extra)
        if character is not None:
            dmg = apply_incoming_damage(pre, character, min_damage=1)
        else:
            dmg = pre
        mult = _enemy_mult(session, i)
        dmg = max(10, int(round(dmg * mult)))
        _apply_damage_to_player(session, dmg, cause="npc", killer_name=_enemy_label(
            session.enemy_kinds[i] if i < len(session.enemy_kinds) else "dark_stalker", npc=True
        ))
        if dmg <= 0:
            notes.append(f"Вражеский огонь ({weapon}): броня сбила удар.")
        else:
            notes.append(f"Вражеский огонь ({weapon}): −{dmg} HP.")
    return notes


def _controller_passives(storage: Storage, session: LabSession) -> list[str]:
    """Пассив контролёра в лабе (как в квестовых вылазках): аура −HP и сбой взгляда."""
    if "controller" not in session.enemy_kinds:
        return []
    notes = apply_controller_aura_to_hp_map(
        session.hp,
        session.player_ids,
        session.enemy_kinds,
        death_causes=session.death_causes,
        death_killers=session.death_killers,
    )
    notes.extend(apply_mutant_turn_effects(storage, session.telegram_id, session))
    return notes


def _mutant_step(
    storage: Storage,
    session: LabSession,
    character: Character | None,
    *,
    kind: str,
    pos: tuple[int, int],
    player_pos: tuple[int, int],
    occupied: set[tuple[int, int]],
    player_cells: set[tuple[int, int]],
    notes: list[str],
    mult: float,
) -> tuple[int, int]:
    if mutant_can_melee_attack(session, kind, pos):
        notes.append(_mutant_attack(storage, session, character, kind=kind, pos=pos, mult=mult))
        return pos
    if mutant_can_ranged_attack(session, kind, pos):
        notes.append(_mutant_attack(storage, session, character, kind=kind, pos=pos, mult=mult, ranged=True))
        return pos
    if not mutant_should_move_when_chasing(session, kind, pos):
        return pos
    target = mutant_chase_target(session, kind)
    candidates = [
        cell
        for cell in mutant_hostile_move_candidates(kind, pos, session.grid)
        if cell not in occupied and cell not in player_cells
    ]
    step = mutant_pick_move_step(session, kind, pos, target, candidates)
    if step is None or step == pos:
        return pos
    # Псевдособака: второй рывок по той же прямой (как в квестовых вылазках).
    extra = mutant_extra_move_step(
        kind,
        pos,
        step,
        target,
        grid=session.grid,
        occupied=occupied,
        player_pos=player_pos,
    )
    return extra or step


def _npc_step(
    session: LabSession,
    character: Character | None,
    *,
    kind: str,
    pos: tuple[int, int],
    weapon: str | None,
    player_pos: tuple[int, int],
    occupied: set[tuple[int, int]],
    player_cells: set[tuple[int, int]],
    notes: list[str],
    mult: float,
) -> tuple[int, int]:
    if manhattan_distance(pos, player_pos) == 1:
        notes.append(_npc_melee_attack(session, character, kind=kind, weapon=weapon, mult=mult))
        return pos
    if random.random() > LAB_NPC_MOVE_CHANCE:
        return pos
    return best_step_toward(
        pos,
        player_pos,
        grid=session.grid,
        blocked=occupied,
        forbidden=player_cells,
    )


def _hostile_turn(storage: Storage, session: LabSession) -> list[str]:
    """Ход врагов: мутанты (свои способности) + НПС (движение и стрельба)."""
    notes: list[str] = []
    tid = session.telegram_id
    player_pos = session.pos(tid)
    player_cells: set[tuple[int, int]] = {player_pos}
    character = storage.get_character(tid, refresh_energy=False)
    occupied = _enemy_cells(session)

    moved: list[list[int]] = []
    for i, pos_raw in enumerate(session.enemies):
        pos = (int(pos_raw[0]), int(pos_raw[1]))
        etype = session.enemy_types[i] if i < len(session.enemy_types) else "mutant"
        kind = session.enemy_kinds[i] if i < len(session.enemy_kinds) else "blind_dog"
        weapon = session.enemy_weapons[i] if i < len(session.enemy_weapons) else None
        mult = _enemy_mult(session, i)
        occupied.discard(pos)
        if etype == "mutant":
            nxt = _mutant_step(
                storage,
                session,
                character,
                kind=kind,
                pos=pos,
                player_pos=player_pos,
                occupied=occupied,
                player_cells=player_cells,
                notes=notes,
                mult=mult,
            )
        else:
            nxt = _npc_step(
                session,
                character,
                kind=kind,
                pos=pos,
                weapon=weapon,
                player_pos=player_pos,
                occupied=occupied,
                player_cells=player_cells,
                notes=notes,
                mult=mult,
            )
        moved.append([int(nxt[0]), int(nxt[1])])
        occupied.add(nxt)
    session.enemies = moved
    notes.extend(_npc_shots(storage, session))
    notes.extend(_controller_passives(storage, session))
    return notes


# ---------------------------------------------------------------------------
# Финализация.
# ---------------------------------------------------------------------------

def _finalize_lab_success(storage: Storage, session: LabSession, *, text: str) -> ActionResult:
    tid = session.telegram_id
    finalize_group_tactical_hp(
        storage,
        session,
        cause_default="lab",
        commit_field_deaths=False,
    )
    _mark_lab_done(storage, session.lab_id, tid)
    session.finished = True
    session.success = True
    session.stage = "done"
    save_lab_session(storage, session)
    clear_lab_session(storage, tid)
    return ActionResult(True, text, payload={"lab_done": True, "lab_active": False})


def _finalize_lab_fail(
    storage: Storage,
    session: LabSession,
    *,
    reason: str,
    cause: str = "lab",
    killer_name: str | None = None,
) -> ActionResult:
    tid = session.telegram_id
    if session.stage == "ambush_combat":
        session.ambush_state = "lost"
    session.finished = True
    session.success = False
    session.stage = "done"
    save_lab_session(storage, session)
    clear_lab_session(storage, tid)
    finalize_group_tactical_hp(
        storage,
        session,
        cause_default=cause,
        commit_field_deaths=True,
    )
    _ = killer_name
    return ActionResult(
        False,
        reason,
        payload={
            "lab_dead": True,
            "lab_active": False,
            "death_location": session.location,
            "death_cause": cause,
        },
    )


def _check_end(storage: Storage, session: LabSession) -> ActionResult | None:
    tid = session.telegram_id
    if session.hp.get(str(tid), 0) <= 0:
        cause = session.death_causes.get(str(tid), "lab")
        return _finalize_lab_fail(
            storage,
            session,
            reason=(
                f"Ты пал в «{session.label}» на уровне {session.level}.\n"
                "Медики заберут тебя на базу — артефакт придётся доставать заново."
            ),
            cause=cause,
            killer_name=session.death_killers.get(str(tid)),
        )
    if session.stage == "ambush_combat" and not session.enemies:
        return _finalize_ambush_win(storage, session)
    return None


def _finalize_ambush_win(storage: Storage, session: LabSession) -> ActionResult:
    session.ambush_state = "won"
    text = (
        "🏆 Засада отбита! Шесть хай-сталкеров лежат в пыли.\n"
        f"Документы и артефакт «{session.label}» остаются у тебя (лут с трупов — уже в инвентаре).\n"
        f"✅ Лаборатория «{session.label}» пройдена."
    )
    return _finalize_lab_success(storage, session, text=text)


# ---------------------------------------------------------------------------
# Старт / переходы / интеракции.
# ---------------------------------------------------------------------------

def start_lab(storage: Storage, telegram_id: int, lab_id: str = "limit1") -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if is_traveling(player):
        return ActionResult(False, "Нельзя идти в лабораторию в пути.")

    session = get_lab_session(storage, telegram_id)
    if session is not None:
        player = storage.get_character(telegram_id, refresh_energy=False) or player
        image = render_lab_for_player(storage, telegram_id, session, player)
        return ActionResult(
            True,
            f"Продолжай вылазку в «{session.label}».",
            payload={
                "lab_image": image,
                "lab_active": True,
                "lab_stage": session.stage,
                "caption": lab_status_caption(session, player),
            },
        )

    lab_cfg = _lab_def(lab_id)
    if player.location != lab_cfg["location"]:
        return ActionResult(False, f"Лаборатория доступна только из «{lab_cfg['location']}».")

    from app.player_busy import player_busy_reason

    busy = player_busy_reason(storage, telegram_id, skip="lab", auto_recover=False)
    if busy:
        return ActionResult(False, busy)

    if not storage.spend_energy(telegram_id, LAB_ENERGY_COST):
        return ActionResult(False, f"Не хватает энергии для входа в лабораторию (нужно {LAB_ENERGY_COST}).")

    session = LabSession(
        session_id=uuid.uuid4().hex[:12],
        telegram_id=int(telegram_id),
        player_ids=[int(telegram_id)],
        lab_id=lab_id,
        label=str(lab_cfg.get("label") or "Предел-1"),
        location=str(lab_cfg.get("location") or "Темная долина"),
        started_at=_utc_now().isoformat(),
    )
    session.hp[str(telegram_id)] = int(player.health)
    _load_level(session, telegram_id, 1, new_floor=False)
    session.log.append(
        "Уровень 1: коридор и комнаты. В одной из комнат — усиленные бойцы Монолита с ключ-картой."
    )
    save_lab_session(storage, session)
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = render_lab_for_player(storage, telegram_id, session, player)
    level_intro = (
        f"🧪 «{session.label}»: лаборатория в «{session.location}».\n"
        "Уровень 1: коридор и комнаты. Зачисти комнату с усиленными бойцами Монолита — "
        "у них ключ-карта (помечается сама, это не предмет). Открой дверь в конце коридора.\n"
        "Зелёные отметки — лут, синяя рамка — дверь. Враги имеют HP — стреляй несколько раз."
    )
    return ActionResult(
        True,
        f"Энергия −{LAB_ENERGY_COST}.\n" + level_intro,
        payload={
            "lab_image": image,
            "lab_active": True,
            "lab_started": True,
            "lab_stage": session.stage,
            "caption": lab_status_caption(session, player),
        },
    )


def lab_abandon(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_lab_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной вылазки в «Предел-1» нет.")
    from app.tactical_hp import sync_session_hp_to_db

    sync_session_hp_to_db(storage, telegram_id, int(session.hp.get(str(telegram_id), 0)), force=True)
    clear_lab_session(storage, telegram_id)
    return ActionResult(
        True,
        "Ты свалил из «{session.label}». Добытые предметы остаются в инвентаре.",
        payload={"lab_active": False},
    )


def lab_move(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_lab_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активной вылазки в «Предел-1».")
    if session.stage not in ("combat", "ambush_combat"):
        return ActionResult(False, "Сейчас нельзя передвигаться.")
    if session.hp.get(str(telegram_id), 0) <= 0:
        return ActionResult(False, "Ты без сознания.")
    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return ActionResult(False, "Некорректное направление.")
    turn_seq = session.turn_seq
    pos = session.pos(telegram_id)
    nxt = (pos[0] + delta[0], pos[1] + delta[1])
    if not (0 <= nxt[0] < session.grid and 0 <= nxt[1] < session.grid):
        return _frame_result(session, telegram_id, "Край поля.")
    if nxt in _enemy_cells(session):
        return _frame_result(session, telegram_id, "Клетка занята врагом — отстреливайся.")
    if not _is_walkable(session, nxt):
        return _frame_result(session, telegram_id, "Там стена.")

    player = storage.get_character(telegram_id, refresh_energy=False)
    session.set_pos(telegram_id, nxt)
    session.player_facing = direction
    # Ближний бой: подойдя к врагу вплотную, игрок бьёт ножом до хода врагов.
    melee_note = ""
    if session.stage in ("combat", "ambush_combat") and session.enemies:
        for idx, e_raw in enumerate(session.enemies):
            e_pos = (int(e_raw[0]), int(e_raw[1]))
            if manhattan_distance(nxt, e_pos) == 1 and int(session.enemy_hp[idx] or 0) > 0:
                knife = max(3, int(round(weapon_damage("Нож"))))
                session.enemy_hp[idx] = max(0, int(session.enemy_hp[idx]) - knife)
                label = _enemy_label(session.enemy_kinds[idx] if idx < len(session.enemy_kinds) else "mutant")
                melee_note = f"🗡 Ближний бой: {label} —{knife} HP."
                break
    hazard_cells = set((_lab_level_cfg(session.lab_id, 1).get("hazard_cells") or ())) if session.level == 1 else set()
    if session.stage == "combat" and nxt in hazard_cells:
        _apply_damage_to_player(session, LAB_HAZARD_DAMAGE, cause="hazard", killer_name="Аномальная растяжка")
        session.log.append(f"⚠ Растяжка! −{LAB_HAZARD_DAMAGE} HP.")
    _advance(session)
    _update_triggers(storage, session)
    done = _after_player_turn(storage, session)
    if done:
        return done
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = render_lab_for_player(storage, telegram_id, session, player)
    note = melee_note or (session.log[-1] if session.log else "Шаг.")
    return ActionResult(
        True,
        "Шаг.",
        payload={
            "lab_image": image,
            "lab_active": True,
            "lab_stage": session.stage,
            "caption": lab_status_caption(session, player),
            "lab_note": note,
        },
    )


def lab_shoot(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_lab_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активной вылазки в «Предел-1».")
    if session.stage not in ("combat", "ambush_combat"):
        return ActionResult(False, "Сейчас нельзя стрелять.")
    if session.hp.get(str(telegram_id), 0) <= 0:
        return ActionResult(False, "Ты без сознания.")
    if direction not in MOVE_DELTAS:
        return ActionResult(False, "Некорректный выстрел.")
    if not session.enemies:
        return _frame_result(session, telegram_id, "Врагов на поле нет — стрелять некого.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")
    weapon = str(player.equipment.get("weapon", "Нож"))
    if weapon_shoot_range(weapon) <= 0:
        return _frame_result(session, telegram_id, "Это оружие не стреляет на дистанции.")
    ammo_result = consume_shot_ammo(storage, telegram_id, weapon)
    if ammo_result is not None:
        return ammo_result
    turn_seq = session.turn_seq
    origin = session.pos(telegram_id)
    cover = _cover_set(session)
    targets = {tuple(e): "enemy" for e in session.enemies}
    hits = collect_player_shot_hits(
        origin,
        direction,
        grid=session.grid,
        weapon_name=weapon,
        blockers=cover,
        targets=targets,
        cover=cover,
    )
    notes: list[str] = []
    if hits:
        any_hit = False
        for hit_cell, _kind in hits:
            idx = _enemy_index_at(session, hit_cell)
            if idx is None:
                continue
            any_hit = True
            raw = weapon_damage(weapon)
            session.enemy_hp[idx] = max(0, session.enemy_hp[idx] - raw)
            label = _enemy_label(
                session.enemy_kinds[idx] if idx < len(session.enemy_kinds) else "mutant",
                npc=(session.enemy_types[idx] == "npc") if idx < len(session.enemy_types) else False,
            )
            if session.enemy_hp[idx] <= 0:
                killed = _remove_enemy_at_index(session, idx)
                if session.stage == "ambush_combat":
                    loot = grant_combat_loot(storage, telegram_id, npc=True)
                    loot_txt = f" Лут: {loot}." if loot else ""
                else:
                    loot_txt = ""
                notes.append(f"{h(player.nickname)} поразил {killed} ({weapon}).{loot_txt}")
            else:
                notes.append(
                    f"{h(player.nickname)} попал по {label}: −{raw} HP "
                    f"({session.enemy_hp[idx]}/{session.enemy_max_hp[idx]})."
                )
        if not any_hit:
            notes.append(f"{h(player.nickname)} промахнулся ({weapon}).")
    else:
        notes.append(f"{h(player.nickname)} промахнулся ({weapon}).")
    session.player_facing = direction
    session.log.extend(notes)
    _update_triggers(storage, session)
    if session.stage == "ambush_combat" and not session.enemies:
        # Засада отбита сразу после последнего выстрела — лут уже выдан выше.
        session.ambush_state = "won"
        return _finalize_ambush_win(storage, session)
    _advance(session)
    done = _after_player_turn(storage, session)
    if done:
        return done
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = render_lab_for_player(storage, telegram_id, session, player)
    note = " ".join(notes)
    return ActionResult(
        True,
        note,
        payload={
            "lab_image": image,
            "lab_active": True,
            "lab_stage": session.stage,
            "caption": lab_status_caption(session, player),
            "lab_note": note,
        },
    )


def lab_medkit(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_lab_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активной вылазки в «Предел-1».")
    if session.stage not in ("combat", "ambush_combat"):
        return ActionResult(False, "Сейчас нельзя лечиться.")
    if session.hp.get(str(telegram_id), 0) <= 0:
        return ActionResult(False, "Ты без сознания.")
    turn_seq = session.turn_seq
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")
    current_hp = session.hp.get(str(telegram_id), int(player.health))
    result, new_hp, item_key = plan_tactical_medkit(storage, telegram_id, int(current_hp))
    if not result.ok:
        return result
    session.hp[str(telegram_id)] = new_hp
    session.log.append(result.text)
    _advance(session)
    _update_triggers(storage, session)
    done = _after_player_turn(storage, session)
    if done:
        if item_key:
            apply_tactical_medkit_spend(storage, telegram_id, item_key, result)
        return done
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    if item_key:
        apply_tactical_medkit_spend(storage, telegram_id, item_key, result)
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = render_lab_for_player(storage, telegram_id, session, player)
    return ActionResult(
        True,
        result.text,
        payload={
            "lab_image": image,
            "lab_active": True,
            "lab_stage": session.stage,
            "caption": lab_status_caption(session, player),
            "lab_note": result.text,
        },
    )


def lab_interact(storage: Storage, telegram_id: int) -> ActionResult:
    """Обыск клетки: лут / записка / дверь уровня (переход, награда)."""
    session = get_lab_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активной вылазки в «Предел-1».")
    if session.stage == "ambush_choice":
        return ActionResult(
            False,
            "Сначала реши, что делать с артефактом — отдать или драться.",
            payload={"lab_ambush_choice": True, "lab_stage": "ambush_choice"},
        )
    if session.stage != "combat":
        return ActionResult(False, "Сейчас не до обысков.")
    if session.hp.get(str(telegram_id), 0) <= 0:
        return ActionResult(False, "Ты без сознания.")
    turn_seq = session.turn_seq
    pos = session.pos(telegram_id)
    cell_key = f"{pos[0]},{pos[1]}"
    cfg = _lab_level_cfg(session.lab_id, session.level)
    lab_cfg = _lab_def(session.lab_id)
    player = storage.get_character(telegram_id, refresh_energy=False)

    # Лут-клетка.
    loot_map = cfg.get("loot_cells") or {}
    if cell_key in loot_map and not session.collected.get(f"loot:{cell_key}"):
        item_key = str(loot_map[cell_key])
        storage.add_item(telegram_id, item_key, 1)
        session.collected[f"loot:{cell_key}"] = True
        label = ITEM_LABELS.get(item_key, item_key)
        session.log.append(f"🧪 Обыск: найден {label} x1.")
        if not _save_turn(storage, session, turn_seq):
            return ActionResult(False, STALE_TURN_MESSAGE)
        image = render_lab_for_player(storage, telegram_id, session, player or storage.get_character(telegram_id, refresh_energy=False))
        return ActionResult(
            True,
            f"🧪 Обыск: найден {label} x1.",
            payload={
                "lab_image": image,
                "lab_active": True,
                "lab_stage": session.stage,
                "caption": lab_status_caption(session, player),
                "lab_note": f"Обыск: найден {label} x1.",
            },
        )

    # Записка (уровень 2).
    note_map = cfg.get("note_cells") or {}
    if cell_key in note_map and not session.collected.get(str(note_map[cell_key])):
        trigger = str(note_map[cell_key])
        session.collected[trigger] = True
        if trigger == "note_level2":
            note_text = str(lab_cfg.get("note_level2_text") or NOTE_LEVEL2_TEXT)
            note_author = str(lab_cfg.get("note_author") or "Бунзова")
            session.log.append(f"📄 Ты прочитал записку {note_author}.")
            if not _save_turn(storage, session, turn_seq):
                return ActionResult(False, STALE_TURN_MESSAGE)
            return ActionResult(
                True,
                "📄 Записка:\n" + note_text,
                payload={
                    "lab_active": True,
                    "lab_stage": session.stage,
                    "lab_note": note_text,
                },
            )

    # Дверь уровня.
    door = tuple(cfg.get("exit_door") or (4, 0))
    if pos == door:
        return _try_enter_door(storage, session, turn_seq)

    return _frame_result(session, telegram_id, "Здесь нечего искать.")


def _try_enter_door(storage: Storage, session: LabSession, turn_seq: int) -> ActionResult:
    cfg = _lab_level_cfg(session.lab_id, session.level)
    lock = dict(cfg.get("door_lock") or {"type": "none"})
    lock_type = str(lock.get("type") or "none")
    tid = session.telegram_id

    if lock_type == "key_item":
        item = str(lock.get("item") or "keycard")
        if not session.collected.get(item):
            return _frame_result(
                session,
                tid,
                "Дверь заперта. Нужна ключ-карта — она у усиленных бойцов Монолита в комнате слева.",
            )
    elif lock_type == "boss_kill":
        if not session.boss_killed:
            return _frame_result(
                session,
                tid,
                "Дверь заперта: система требует уничтожить образец на этом уровне.",
            )
    else:
        if session.enemies:
            return _frame_result(
                session,
                tid,
                "Дверь открыта, но сначала зачисти лабораторию от врагов.",
            )

    door_id = f"lvl{session.level}_door"
    if door_id not in session.opened_doors:
        session.opened_doors.append(door_id)

    if session.level == 1:
        return _enter_next_level(storage, session, 2, turn_seq)
    if session.level == 2:
        return _enter_next_level(storage, session, 3, turn_seq)
    return _grant_level3_reward(storage, session, turn_seq)


def _enter_next_level(storage: Storage, session: LabSession, level: int, turn_seq: int) -> ActionResult:
    tid = session.telegram_id
    door_text = f"🚪 Дверь открыта! «{_lab_level_cfg(session.lab_id, session.level).get('label', session.label)}» пройден."
    _load_level(session, tid, level, new_floor=True)
    intro = _level_intro(session.lab_id, level)
    session.log.append(f"Уровень {level}: {intro}")
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    player = storage.get_character(tid, refresh_energy=False)
    image = render_lab_for_player(storage, tid, session, player) if player is not None else None
    # Иконки мутантов уровня (не больше 3) — подсказки способностей, как в квестовых вылазках.
    hints = mutant_field_ability_warnings(session.enemy_kinds)[:3]
    body = door_text + "\n\n" + intro
    if hints:
        body += "\n" + "\n".join(hints)
    return ActionResult(
        True,
        body,
        payload={
            "lab_image": image,
            "lab_transition": True,
            "lab_stage": "transition",
            "lab_active": True,
            "caption": lab_status_caption(session, player),
        },
    )


def lab_enter_level(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_lab_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активной вылазки в «Предел-1».")
    if session.stage != "transition":
        return ActionResult(False, "Некуда входить.", payload={"lab_stage": session.stage})
    turn_seq = session.turn_seq
    session.stage = "combat"
    session.turn_deadline = _deadline_iso(LAB_TURN_SECONDS)
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")
    image = render_lab_for_player(storage, telegram_id, session, player)
    return ActionResult(
        True,
        "Входишь на уровень " + str(session.level) + ".",
        payload={
            "lab_image": image,
            "lab_active": True,
            "lab_stage": session.stage,
            "caption": lab_status_caption(session, player),
        },
    )


def _grant_level3_reward(storage: Storage, session: LabSession, turn_seq: int) -> ActionResult:
    tid = session.telegram_id
    art_key = f"artifact_predel{session.lab_id[-1]}"
    if not session.collected.get("reward_level3"):
        storage.add_item(tid, "intel_document", 1)
        storage.add_item(tid, art_key, 1)
        session.collected["reward_level3"] = True
    session.stage = "ambush_choice"
    session.ambush_state = "pending"
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    _mark_lab_done(storage, session.lab_id, tid)
    final_note = str(_lab_def(session.lab_id).get("note_final_text") or NOTE_FINAL_TEXT)
    doc_label = ITEM_LABELS.get("intel_document", "Документы")
    art_label = ITEM_LABELS.get(art_key, f"Артефакт «{session.label}»")
    text = (
        f"🚪 Дверь открыта — комната с наградой!\n"
        f"+{doc_label} x1.\n"
        f"+{art_label} x1.\n\n"
        f"Записка:\n«{final_note}»\n\n"
        f"⚠️ {AMBUSH_INTRO_TEXT}"
    )
    payload: dict[str, Any] = {"lab_ambush_choice": True, "lab_stage": "ambush_choice", "lab_active": True}
    player = storage.get_character(tid, refresh_energy=False)
    if player is not None:
        try:
            payload["lab_image"] = render_lab_for_player(storage, tid, session, player)
            payload["caption"] = lab_status_caption(session, player)
        except Exception:
            payload.pop("lab_image", None)
    return ActionResult(
        True,
        text,
        payload=payload,
    )


def lab_ambush_choice(storage: Storage, telegram_id: int, *, give_up: bool) -> ActionResult:
    session = get_lab_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активной вылазки в «Предел-1».")
    if session.stage != "ambush_choice":
        return ActionResult(False, "Засада уже решена.")

    if give_up:
        storage.remove_item(telegram_id, f"artifact_predel{session.lab_id[-1]}", 1)
        storage.remove_item(telegram_id, "intel_document", 1)
        session.ambush_state = "gave_up"
        text = (
            "🕊 Ты отдал артефакт и документы хай-сталкерам. Они молча пропускают тебя.\n"
            "Артефакт и документы ушли из инвентаря — но факт прохождения лабы сохранён "
            "(для будущей сборки мега-артефакта)."
        )
        return _finalize_lab_success(storage, session, text=text)

    # Принять бой.
    turn_seq = session.turn_seq
    _spawn_ambush(session, telegram_id)
    session.log.append(AMBUSH_INTRO_TEXT)
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    player = storage.get_character(telegram_id, refresh_energy=False)
    image = render_lab_for_player(storage, telegram_id, session, player or storage.get_character(telegram_id, refresh_energy=False))
    return ActionResult(
        True,
        "⚔️ Засада! 6 хай-сталкеров на арене 7×7. Держись!",
        payload={
            "lab_image": image,
            "lab_active": True,
            "lab_stage": session.stage,
            "caption": lab_status_caption(session, player),
        },
    )


def _frame_result(
    session: LabSession,
    telegram_id: int,
    text: str,
    *,
    image: bytes | None = None,
    caption: str = "",
) -> ActionResult:
    payload: dict[str, Any] = {"lab_active": True, "lab_stage": session.stage}
    if image is not None:
        payload["lab_image"] = image
    if caption:
        payload["caption"] = caption
    return ActionResult(False, text, payload=payload)


def _after_player_turn(storage: Storage, session: LabSession) -> ActionResult | None:
    session.log.extend(_hostile_turn(storage, session))
    _update_triggers(storage, session)
    return _check_end(storage, session)


# ---------------------------------------------------------------------------
# Тексты и рендер.
# ---------------------------------------------------------------------------

def lab_status_caption(session: LabSession, character: Character | None = None) -> str:
    tid = session.telegram_id
    if session.stage == "ambush_combat":
        head = "⚔️ Засада хай-сталкеров"
    elif session.stage == "ambush_choice":
        head = f"🧪 «{session.label}» пройден — засада у выхода"
    else:
        head = f"🧪 «{session.label}» · Уровень {session.level}"
    lines = [head]
    lines.append(f"Врагов: {len(session.enemies)}")
    if session.enemies:
        seen: list[str] = []
        for i, kind in enumerate(session.enemy_kinds):
            label = _enemy_label(kind, npc=(i < len(session.enemy_types) and session.enemy_types[i] == "npc"))
            if label not in seen:
                seen.append(label)
        lines.append("Враги: " + ", ".join(seen))
    if session.collected.get("keycard"):
        lines.append("🗝 Ключ-карта получена.")
    if session.collected.get("note_level2"):
        note_author = str(_lab_def(session.lab_id).get("note_author") or "Бунзова")
        lines.append(f"📄 Записка {note_author} прочитана.")
    if session.boss_killed:
        lines.append("👹 Образец уничтожен — дверь открыта.")
    if session.collected.get("reward_level3"):
        lines.append(f"🧭 Артефакт «{session.label}» и документы у тебя.")
    hp = session.hp.get(str(tid), 0)
    if character is not None:
        lines.append(f"HP {hp}/{effective_max_health(character)} · ⚡ {character.energy}")
    else:
        lines.append(f"HP {hp}")
    if session.log:
        lines.append(h(session.log[-1][:80]))
    lines.append("⭢ ход · 🔫 выстрел · 🧪 обыск/дверь · 💉 аптечка")
    return "\n".join(lines)


LAB_RENDER_CELL = 60


def render_lab_for_player(storage: Storage, telegram_id: int, session: LabSession, player: Character) -> bytes:
    return render_lab_frame(
        storage,
        session,
        player,
        rating_points=_lab_rating(storage, telegram_id),
    )


def _lab_rating(storage: Storage, telegram_id: int) -> int:
    try:
        return max(0, int(storage.get_player_stats(telegram_id).get("rating_points", 0)))
    except Exception:
        return 0


def _load_font(size: int) -> ImageFont.ImageFont:
    return load_tactical_font(size)


def _paste_lab_npc_sprite(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    cx: int,
    cy: int,
    kind: str,
    diameter: int,
    ring_color: tuple[int, int, int],
) -> None:
    """Спрайт НПС по своему виду (в лабах — Монолит), а не общий стуб «Малого».

    paste_npc_sprite из tactical_render игнорирует kind и всегда рисует
    «Малого», поэтому в лабах спрайт берём напрямую из npc_assets по kind.
    """
    sprite = npc_sprite_image(kind)
    if sprite is None:
        paste_npc_sprite(canvas, draw, cx=cx, cy=cy, kind=kind, diameter=diameter, ring_color=ring_color)
        return
    _paste_circle(canvas, sprite, cx, cy, diameter, ring_color=ring_color, ring_width=2)


def _apply_lab_fog(canvas: Image.Image, *, margin: int, grid_px: int, seed: int = 0) -> None:
    """Декоративный серый туман над полем (только уровень 1) — не влияет на геймплей, только атмосфера."""
    if grid_px <= 0:
        return
    rng = random.Random(seed)
    fog = Image.new("RGBA", (grid_px, grid_px), (0, 0, 0, 0))
    fog_draw = ImageDraw.Draw(fog)
    blob_count = max(10, grid_px // 24)
    for _ in range(blob_count):
        bx = rng.randint(0, grid_px)
        by = rng.randint(0, grid_px)
        radius = rng.randint(grid_px // 10, grid_px // 5)
        shade = rng.randint(180, 225)
        alpha = rng.randint(50, 90)
        fog_draw.ellipse(
            (bx - radius, by - radius, bx + radius, by + radius),
            fill=(shade, shade, shade, alpha),
        )
    fog = fog.filter(ImageFilter.GaussianBlur(radius=max(4, grid_px // 40)))
    canvas.alpha_composite(fog, (margin, margin))


def render_lab_frame(
    storage: Storage,
    session: LabSession,
    character: Character | None = None,
    *,
    rating_points: int = 0,
) -> bytes:
    cell = 60 if session.grid >= 9 else 80
    grid = session.grid
    grid_px = grid * cell
    margin = 16
    panel_w = 300
    width = margin + grid_px + 14 + panel_w + margin
    height = max(margin + grid_px + margin, 640)
    canvas = Image.new("RGBA", (width, height), (16, 18, 20, 255))
    draw = ImageDraw.Draw(canvas)

    field = (margin - 6, margin - 6, margin + grid_px + 6, margin + grid_px + 6)
    draw.rounded_rectangle(field, radius=10, fill=(34, 36, 40, 255), outline=(70, 74, 80), width=2)

    has_photo = paste_location_field_photo(canvas, session.location, margin=margin, grid_px=grid_px, alpha=150)

    walkable = _level_walkable(session)
    wall = (34, 32, 32)
    floor_tone = (62, 66, 70)
    room_tint = {
        "hostile": (92, 52, 52),
        "loot": (48, 80, 52),
        "cleared": (70, 74, 78),
    }
    room_cells = _level_room_cells(session)
    tid = session.telegram_id
    player_pos = session.pos(tid)
    cover = _cover_set(session)
    cfg_for_hazard = _lab_level_cfg(session.lab_id, session.level) if session.stage != "ambush_combat" else None
    hazard_cells = (
        {(int(c[0]), int(c[1])) for c in (cfg_for_hazard.get("hazard_cells") or ())}
        if cfg_for_hazard is not None
        else set()
    )
    for gy in range(grid):
        for gx in range(grid):
            cell_pos = (gx, gy)
            left = margin + gx * cell
            top = margin + gy * cell
            if cell_pos not in walkable:
                draw.rectangle((left, top, left + cell - 1, top + cell - 1), fill=wall)
                draw.rectangle((left, top, left + cell - 1, top + cell - 1), outline=(28, 30, 34), width=1)
                continue
            tone = floor_tone
            for room_id, cells in room_cells.items():
                if any(int(c[0]) == gx and int(c[1]) == gy for c in cells):
                    tone = room_tint.get(session.rooms.get(room_id, ""), floor_tone)
                    break
            fill = tuple(int(v) for v in tone)
            if has_photo:
                overlay = Image.new("RGBA", (cell, cell), (10, 12, 14, 60))
                canvas.alpha_composite(overlay, (left, top))
            else:
                draw.rectangle((left, top, left + cell - 1, top + cell - 1), fill=fill)
            draw.rectangle((left, top, left + cell - 1, top + cell - 1), outline=(28, 30, 34), width=1)
            if cell_pos in cover:
                draw.rounded_rectangle(
                    (left + 4, top + 4, left + cell - 5, top + cell - 5),
                    radius=6,
                    fill=(70, 62, 48, 220),
                    outline=(100, 90, 70),
                )
            if cell_pos in hazard_cells:
                hazard_overlay = Image.new("RGBA", (cell, cell), (255, 170, 20, 90))
                canvas.alpha_composite(hazard_overlay, (left, top))
                draw.rectangle(
                    (left + 2, top + 2, left + cell - 3, top + cell - 3),
                    outline=(255, 200, 40),
                    width=3,
                )

    if cfg_for_hazard is not None and cfg_for_hazard.get("fog") and session.level == 1:
        _apply_lab_fog(canvas, margin=margin, grid_px=grid_px, seed=int(session.turn_seq))

    # Спецклетки: лут/записка/дверь.
    small = _load_font(10)
    cfg = _lab_level_cfg(session.lab_id, session.level) if session.stage != "ambush_combat" else None
    if cfg is not None:
        loot_map = cfg.get("loot_cells") or {}
        for key, _item_key in loot_map.items():
            if session.stage == "ambush_combat":
                break
            cx, cy = (int(p) for p in key.split(","))
            if session.collected.get(f"loot:{key}"):
                continue
            left = margin + cx * cell
            top = margin + cy * cell
            draw.rounded_rectangle(
                (left + 3, top + 3, left + cell - 4, top + cell - 4),
                radius=6,
                outline=(80, 220, 90),
                width=2,
            )
            draw.text((left + 6, top + cell - 14), "ЛУТ", fill=(140, 230, 150), font=small)
        note_map = cfg.get("note_cells") or {}
        for key, trigger in note_map.items():
            if session.collected.get(str(trigger)):
                continue
            cx, cy = (int(p) for p in key.split(","))
            left = margin + cx * cell
            top = margin + cy * cell
            # Жёлтые маркеры на поле убраны по жалобе игроков — записка
            # помечается бледно-синей рамкой (дверь — синяя, лут — зелёный).
            draw.rounded_rectangle(
                (left + 3, top + 3, left + cell - 4, top + cell - 4),
                radius=6,
                outline=(215, 225, 240),
                width=2,
            )
            draw.text((left + 6, top + cell - 14), "ЗАПИСЬ", fill=(225, 235, 250), font=small)
        door = tuple(cfg.get("exit_door") or (4, 0))
        dx, dy = int(door[0]), int(door[1])
        left = margin + dx * cell
        top = margin + dy * cell
        draw.rectangle(
            (left + 3, top + 3, left + cell - 4, top + cell - 4),
            outline=(80, 190, 230),
            width=3,
        )
        draw.text((left + 5, top + cell - 14), "ДВЕРЬ", fill=(120, 210, 240), font=small)

    # Плашка врагов (живое HP).
    enemy_slots: list[EnemyHudSlot] = []
    for i, kind in enumerate(session.enemy_kinds):
        is_npc = i < len(session.enemy_types) and session.enemy_types[i] == "npc"
        hp_val = session.enemy_hp[i] if i < len(session.enemy_hp) else 0
        max_hp = session.enemy_max_hp[i] if i < len(session.enemy_max_hp) else hp_val
        enemy_slots.append(EnemyHudSlot(str(kind), bool(is_npc), int(hp_val), int(max_hp)))

    for i, pos_raw in enumerate(session.enemies):
        ex, ey = int(pos_raw[0]), int(pos_raw[1])
        cx = margin + ex * cell + cell // 2
        cy = margin + ey * cell + cell // 2
        kind = session.enemy_kinds[i] if i < len(session.enemy_kinds) else "blind_dog"
        is_npc = i < len(session.enemy_types) and session.enemy_types[i] == "npc"
        if is_npc:
            _paste_lab_npc_sprite(
                canvas,
                draw,
                cx=cx,
                cy=cy,
                kind=kind,
                diameter=MISSION_NPC_GRID_DIAMETER - 8,
                ring_color=(255, 90, 70),
            )
        else:
            paste_mutant_sprite(
                canvas,
                draw,
                cx=cx,
                cy=cy,
                kind=kind,
                diameter=mutant_grid_diameter(kind, default=MISSION_MUTANT_GRID_DIAMETER - 8),
                ring_color=(210, 55, 45),
            )

    # Игрок.
    px, py = player_pos
    pcx = margin + px * cell + cell // 2
    pcy = margin + py * cell + cell // 2
    player_diameter = cell - 6
    hp_now = session.hp.get(str(tid), 0)
    paste_player_avatar(
        canvas,
        draw,
        storage,
        pid=tid,
        cx=pcx,
        cy=pcy,
        diameter=player_diameter,
        ring_color=(72, 220, 90),
        hp=hp_now,
        is_active=True,
        viewer_cell=(margin, cell, px, py),
    )
    # Стрелка направления взгляда.
    draw_face = ImageDraw.Draw(canvas)
    facing = session.player_facing if session.player_facing in MOVE_DELTAS else "down"
    fdx, fdy = MOVE_DELTAS[facing]
    tip = (pcx + fdx * (player_diameter // 2 + 6), pcy + fdy * (player_diameter // 2 + 6))
    base_l = (pcx - fdy * 10 - fdx * 6, pcy + fdx * 10 - fdy * 6)
    base_r = (pcx + fdy * 10 - fdx * 6, pcy - fdx * 10 - fdy * 6)
    draw_face.polygon([tip, base_l, base_r], fill=(255, 230, 90), outline=(50, 45, 30))

    # Правая панель.
    pl = margin + grid_px + 14
    pr = width - margin
    pt = margin - 6
    pb = height - margin + 6
    draw.rounded_rectangle((pl, pt, pr, pb), radius=14, fill=(48, 50, 54, 255), outline=(100, 104, 110), width=2)
    thumb = (pl + 10, pt + 8, pr - 10, pt + 96)
    loc_img = _load_location_thumb(session.location)
    if loc_img is not None:
        _paste_rounded(canvas, loc_img, thumb, radius=8)
        ImageDraw.Draw(canvas).rounded_rectangle(thumb, radius=8, outline=(110, 120, 100), width=2)
    else:
        ImageDraw.Draw(canvas).rounded_rectangle(thumb, radius=8, fill=(30, 34, 28), outline=(90, 100, 80), width=2)

    draw = ImageDraw.Draw(canvas)
    title_font = _load_font(18)
    body = _load_font(14)
    tiny = _load_font(12)
    draw.text((pl + 14, pt + 102), f"{session.label} · Ур.{session.level}", fill=(245, 245, 245), font=title_font)
    y = pt + 128
    draw.text((pl + 14, y), f"Врагов: {len(session.enemies)}", fill=(230, 180, 160), font=body)
    y += 22
    if session.collected.get("keycard"):
        draw.text((pl + 14, y), "🗝 Ключ-карта", fill=(255, 220, 120), font=tiny)
        y += 16
    if session.collected.get("note_level2"):
        note_author = str(_lab_def(session.lab_id).get("note_author") or "Бунзова")
        draw.text((pl + 14, y), f"📄 Записка {note_author}", fill=(240, 215, 120), font=tiny)
        y += 16
    if session.boss_killed:
        draw.text((pl + 14, y), "👹 Образец уничтожен", fill=(220, 120, 120), font=tiny)
        y += 16
    if session.collected.get("reward_level3"):
        draw.text((pl + 14, y), "🧭 Артефакт и доки у тебя", fill=(120, 255, 140), font=tiny)
        y += 16
    y += 8
    hp = hp_now if character is None else int(hp_now)
    max_hp = int(effective_max_health(character)) if character else 100
    energy = int(character.energy) if character else 0
    max_energy = int(character.max_energy) if character else 100
    meds = 0
    if character is not None:
        meds = sum(int(character.inventory.get(k, 0)) for k in ("medkit", "medkit_army", "medkit_science"))
    bar_top = y
    draw.rounded_rectangle((pl + 14, bar_top, pr - 14, bar_top + 24), radius=6, fill=(30, 30, 34), outline=(90, 90, 95))
    fill_w = int((pr - pl - 34) * (hp / max(1, max_hp)))
    if fill_w > 0:
        draw.rounded_rectangle((pl + 16, bar_top + 2, pl + 16 + fill_w, bar_top + 22), radius=4, fill=(200, 60, 50))
    draw.text((pl + 18, bar_top + 4), f"HP {hp}/{max_hp}", fill=(255, 255, 255), font=tiny)
    draw.rounded_rectangle((pl + 14, bar_top + 32, pr - 14, bar_top + 52), radius=6, fill=(30, 30, 34), outline=(90, 90, 95))
    fill_w = int((pr - pl - 34) * min(1.0, energy / max(1, max_energy)))
    if fill_w > 0:
        draw.rounded_rectangle((pl + 16, bar_top + 34, pl + 16 + fill_w, bar_top + 52), radius=4, fill=(50, 120, 210))
    draw.text((pl + 18, bar_top + 36), f"EN {energy}/{max_energy}", fill=(255, 255, 255), font=tiny)
    draw.text((pl + 14, bar_top + 62), f"Аптечки: {meds}", fill=(180, 200, 180), font=tiny)

    if session.stage == "ambush_combat":
        draw.text((pl + 14, pb - 44), "Засада хай-сталкеров", fill=(220, 90, 80), font=body)
    elif session.stage == "ambush_choice":
        draw.text((pl + 14, pb - 44), "Выбор: отдать или драться", fill=(255, 220, 120), font=body)
    else:
        draw.text((pl + 14, pb - 44), "Стрелки — ход · 🔫 — стрельба", fill=(190, 190, 190), font=tiny)
    draw.text((pl + 14, pb - 26), "🧪 — обыск/дверь · 💉 — аптечка", fill=(190, 190, 190), font=tiny)

    draw_enemy_hud(
        canvas,
        enemy_slots,
        panel_left=pl,
        panel_top=pt,
        panel_right=pr,
        panel_bottom=pb,
        y_top=bar_top + 92,
    )

    out = canvas.convert("RGB")
    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _level_room_cells(session: LabSession) -> dict[str, list[list[int]]]:
    """Клетки комнат текущего уровня (для раскраски)."""
    if session.stage == "ambush_combat":
        return {}
    cfg = _lab_level_cfg(session.lab_id, session.level)
    return {str(k): [[int(c[0]), int(c[1])] for c in v] for k, v in (cfg.get("room_cells") or {}).items()}