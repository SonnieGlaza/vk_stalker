from __future__ import annotations

import re
import tempfile
from pathlib import Path

from app.game_logic import (
    ITEM_LABELS,
    SHOP_ITEMS,
    attack_location,
    buy_item,
    build_alliance_overview,
    build_economy_overview,
    build_market_lots_overview,
    build_raids_overview,
    build_war_lobby_overview,
    create_market_lot,
    create_or_join_faction_raid,
    create_or_join_war_lobby,
    launch_open_raid,
    launch_war_lobby,
    repair_truck,
    round_shop_price,
    upgrade_armor,
    armor_defense,
    apply_incoming_damage,
    travel_to,
    use_medkit,
    accept_quest_contract,
    run_contract_work,
    can_travel_by_truck,
)
from app.storage import Storage

PROJECT_ROOT = Path(__file__).resolve().parent


def _all_callback_data() -> set[str]:
    keyboards_source = (PROJECT_ROOT / "app" / "keyboards.py").read_text(encoding="utf-8")
    return set(re.findall(r'callback_data="([^"]+)"', keyboards_source))


def _callback_handler_coverage() -> tuple[set[str], set[str], list[str]]:
    bot_source = (PROJECT_ROOT / "app" / "bot.py").read_text(encoding="utf-8")
    exact_handlers = set(re.findall(r'@router\.callback_query\(F\.data == "([^"]+)"\)', bot_source))
    # Любые F.data.startswith("...") в хендлерах (в т.ч. через |).
    prefix_handlers = set(re.findall(r'F\.data\.startswith\("([^"]+)"\)', bot_source))
    # Exact multi-value filters: F.data.in_({...})
    for block in re.findall(r"@router\.callback_query\(\s*F\.data\.in_\(\s*\{([^}]+)\}\s*\)", bot_source, flags=re.S):
        exact_handlers.update(re.findall(r'"([^"]+)"', block))

    missing: list[str] = []
    for callback_data in sorted(_all_callback_data()):
        if callback_data in exact_handlers:
            continue
        if any(callback_data.startswith(prefix) for prefix in prefix_handlers):
            continue
        # Registration-only callbacks are handled under FSM state filters.
        if callback_data.startswith("gender:"):
            continue
        # Динамические confirm-колбэки покрыты startswith trade:upgrade:
        if callback_data.endswith(":confirm"):
            continue
        missing.append(callback_data)
    return exact_handlers, prefix_handlers, missing


def run_smoke_check() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "smoke.sqlite3"
        snapshot_path = Path(tmp) / "smoke.backup.json"
        storage = Storage(str(db_path), snapshot_path=str(snapshot_path))
        storage.init_db()

        # Registration + factions.
        storage.create_character(111, "LeaderDuty", "Мужской")
        storage.create_character(222, "WingmanDuty", "Мужской")
        storage.create_character(333, "LeaderBandit", "Женский")
        from app.game_logic import STARTING_MONEY_RU

        starter0 = storage.get_character(111, refresh_energy=False)
        assert starter0 is not None
        assert int(starter0.money) == STARTING_MONEY_RU
        assert starter0.equipment.get("weapon") == "ПМ"
        assert int(starter0.inventory.get("medkit", 0)) >= 2
        assert int(starter0.inventory.get("vodka", 0)) >= 2
        storage.set_faction(111, "Долг")
        storage.set_faction(222, "Долг")
        storage.set_faction(333, "Бандиты")
        assert storage.character_exists(111)
        assert storage.character_exists(222)
        assert storage.character_exists(333)
        from app.game_logic import faction_home_base

        assert storage.get_character(111, refresh_energy=False).location == faction_home_base("Долг")
        assert storage.get_character(333, refresh_energy=False).location == faction_home_base("Бандиты")
        assert storage.set_faction_leader("Долг", 111)
        assert storage.set_faction_leader("Бандиты", 333)

        # Faction ranks assigned by leader.
        from app.game_logic import assign_faction_rank, character_rank_title

        assert character_rank_title(storage, storage.get_character(111)) == "Полковник"
        assert character_rank_title(storage, storage.get_character(222)) == "Рядовой"
        promote = assign_faction_rank(storage, 111, 222, "r3")
        assert promote.ok, promote.text
        assert character_rank_title(storage, storage.get_character(222)) == "Прапорщик"
        denied = assign_faction_rank(storage, 222, 111, "r2")
        assert not denied.ok
        bandit_rank = assign_faction_rank(storage, 333, 333, "r1")
        assert not bandit_rank.ok

        # Faction base fortification (+1 defense per fortify cost from treasury).
        from app.game_logic import upgrade_faction_base, BASE_FORTIFY_COST_RU

        duty_base = faction_home_base("Долг")
        assert int(storage.get_location(duty_base).get("defense_bonus") or 0) == 0
        denied_fortify = upgrade_faction_base(storage, 222)
        assert not denied_fortify.ok
        storage.change_faction_treasury("Долг", BASE_FORTIFY_COST_RU * 3)
        before_treasury = next(f for f in storage.get_factions() if f["name"] == "Долг")["treasury"]
        fortify = upgrade_faction_base(storage, 111)
        assert fortify.ok, fortify.text
        after_treasury = next(f for f in storage.get_factions() if f["name"] == "Долг")["treasury"]
        assert before_treasury - after_treasury == BASE_FORTIFY_COST_RU
        assert int(storage.get_location(duty_base)["defense_bonus"]) == 1
        fortify2 = upgrade_faction_base(storage, 111)
        assert fortify2.ok, fortify2.text
        assert int(storage.get_location(duty_base)["defense_bonus"]) == 2

        from app.faction_bots import (
            FACTION_BOT_COUNT_UPGRADE_COST,
            FACTION_BOT_UPGRADE_COST,
            BOT_MONOLITH_WEAPONS,
            bot_weapons_for_tier,
            get_faction_bots,
            upgrade_faction_bot_count,
            upgrade_faction_bots,
        )

        assert BOT_MONOLITH_WEAPONS == ("АК-74", "СПАС-12")
        assert bot_weapons_for_tier(1, faction="Монолит") == BOT_MONOLITH_WEAPONS

        storage.change_faction_treasury("Долг", FACTION_BOT_UPGRADE_COST + FACTION_BOT_COUNT_UPGRADE_COST)
        bots_before = get_faction_bots(storage, "Долг")
        assert int(bots_before["tier"]) == 1
        bot_up = upgrade_faction_bots(storage, 111)
        assert bot_up.ok, bot_up.text
        assert int(get_faction_bots(storage, "Долг")["tier"]) == 2
        bot_cnt = upgrade_faction_bot_count(storage, 111)
        assert bot_cnt.ok, bot_cnt.text
        assert int(get_faction_bots(storage, "Долг")["count"]) == int(bots_before["count"]) + 1
        already_t2 = upgrade_faction_bots(storage, 111)
        assert not already_t2.ok

        from app.faction_bots import apply_location_control, garrison_defenders_for_location

        neutral_for_garrison = next(
            loc["name"]
            for loc in storage.get_locations()
            if not loc.get("controlled_by") and str(loc.get("point_type") or "") != "база"
        )
        apply_location_control(storage, str(neutral_for_garrison), "Долг")
        assert garrison_defenders_for_location(storage, str(neutral_for_garrison), "Долг") >= 1

        # Referral rewards.
        from app.game_logic import (
            apply_referral_rewards,
            build_referral_link,
            parse_referral_payload,
            REFERRAL_INVITER_BONUS_RU,
        )

        assert parse_referral_payload("ref_111") == 111
        assert parse_referral_payload("ref111") == 111
        assert parse_referral_payload("hello") is None
        assert build_referral_link("my_bot", 111) == "https://t.me/my_bot?start=ref_111"
        storage.create_character(444, "NewbieRef", "Мужской")
        before_money = storage.get_character(111, refresh_energy=False).money
        referral = apply_referral_rewards(storage, 444, 111)
        assert referral.ok, referral.text
        newbie = storage.get_character(444, refresh_energy=False)
        # Стартовый набор + реферальный пак.
        assert newbie.inventory.get("stew") == 2
        assert newbie.inventory.get("antirad") == 1
        assert int(newbie.inventory.get("water_bottle") or 0) >= 1
        assert int(newbie.inventory.get("medkit") or 0) >= 1
        assert newbie.inventory.get("weapon_pm", 0) == 0
        assert newbie.equipment.get("weapon") == "ПМ"
        after_money = storage.get_character(111, refresh_energy=False).money
        assert after_money == before_money + REFERRAL_INVITER_BONUS_RU
        again = apply_referral_rewards(storage, 444, 111)
        assert not again.ok

        # Economy / trader items.
        from app.vendors import set_vendor_tier, get_vendor_tier, apply_tech_repair_discount

        # На старте бармен/медик/техник — этап 1.
        assert get_vendor_tier(storage, 111, "barkeep") == 1
        storage.change_money(111, 50_000)
        assert buy_item(storage, 111, "detector_otklik").ok
        assert buy_item(storage, 111, "medkit").ok
        assert not buy_item(storage, 111, "weapon_gauss").ok
        assert not buy_item(storage, 111, "medkit_science").ok
        assert not buy_item(storage, 111, "truck").ok
        # Максимальные этапы для дальнейших smoke-покупок.
        set_vendor_tier(storage, 111, "barkeep", 4)
        set_vendor_tier(storage, 222, "barkeep", 4)
        set_vendor_tier(storage, 333, "barkeep", 4)
        set_vendor_tier(storage, 111, "medic", 4)
        set_vendor_tier(storage, 111, "tech", 4)
        storage.change_money(111, 1000000)
        assert buy_item(storage, 111, "truck").ok
        storage.change_money(222, 200000)
        assert buy_item(storage, 222, "niva").ok
        from datetime import datetime, timedelta, timezone

        storage.change_money(333, 10000)
        bike = buy_item(storage, 333, "bicycle")
        assert bike.ok, bike.text
        assert storage.get_character(333, refresh_energy=False).bicycle_owned
        bike_travel = travel_to(storage, 333, "Янтарь")
        assert bike_travel.ok, bike_travel.text
        assert "велосипед" in bike_travel.text.lower()
        past_bike = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with storage._connect() as conn:
            conn.execute(
                "UPDATE characters SET travel_arrives_at = ? WHERE telegram_id = ?",
                (past_bike, 333),
            )
        storage.resolve_travel_if_due(333)
        assert storage.get_character(333, refresh_energy=False).location == "Янтарь"
        assert storage.get_last_arrival_transport(333) == "bicycle"
        storage.change_diesel(111, 3)
        storage.change_gasoline(222, 5)
        assert buy_item(storage, 222, "gasoline_can").ok
        before_travel = storage.get_character(111, refresh_energy=False)
        assert before_travel is not None
        before_truck_durability = before_travel.truck_durability
        assert buy_item(storage, 111, "sleeping_bag").ok
        storage.change_money(111, 100000)
        bulk = buy_item(storage, 111, "medkit", amount=10)
        assert bulk.ok, bulk.text
        assert "×10" in bulk.text
        assert not buy_item(storage, 111, "detector_otklik", amount=5).ok
        assert use_medkit(storage, 111).ok is False  # hp full

        # Апгрейд медика авторитетом, не за RU.
        from app.vendors import add_vendor_reputation, get_vendor_reputation

        set_vendor_tier(storage, 222, "medic", 1)
        add_vendor_reputation(storage, 222, "medic", 5000)
        assert get_vendor_reputation(storage, 222, "medic") == 5000
        assert get_vendor_tier(storage, 222, "medic") == 4
        assert buy_item(storage, 222, "medkit_science").ok
        assert not buy_item(storage, 222, "antibiotic").ok
        add_vendor_reputation(storage, 222, "medic", 15000)
        assert get_vendor_tier(storage, 222, "medic") == 5
        storage.change_money(222, 5000)
        assert buy_item(storage, 222, "antibiotic").ok
        from app.game_logic import (
            EMISSION_META_AT,
            EMISSION_META_PHASE,
            _kill_players_in_locations,
            _safe_base_location_names,
            has_emission_protection,
            use_antibiotic,
        )

        storage.create_character(555, "EmissionTest", "Мужской")
        storage.change_money(555, 10000)
        storage.add_item(555, "antibiotic", 1)
        with storage._connect() as conn:
            conn.execute(
                "UPDATE characters SET location = ? WHERE telegram_id = ?",
                ("Радар", 555),
            )
        storage.set_meta(EMISSION_META_PHASE, "calm")
        storage.set_meta(
            EMISSION_META_AT,
            (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        )
        assert use_antibiotic(storage, 555).ok
        assert has_emission_protection(storage, 555)
        safe_bases = _safe_base_location_names(storage)
        killed, killed_ids = _kill_players_in_locations(storage, {"Радар"}, safe_bases)
        assert 555 not in killed_ids
        assert storage.get_character(555, refresh_energy=False).health > 0

        # Антибиотик mid-Выброс должен перекрывать оставшиеся волны, не только ближайшую.
        from app.game_logic import (
            EMISSION_META_WAVE_AT,
            EMISSION_PROTECT_BUFFER_MINUTES,
            EMISSION_WAVE_GAP_MINUTES,
            _compute_emission_protect_until,
            _emission_protect_meta_key,
        )

        storage.add_item(555, "antibiotic", 1)
        storage.delete_meta(_emission_protect_meta_key(555))
        now_mid = datetime.now(timezone.utc)
        wave2_at = now_mid + timedelta(minutes=3)
        storage.set_meta(EMISSION_META_PHASE, "wave2")
        storage.set_meta(EMISSION_META_WAVE_AT, wave2_at.isoformat())
        assert use_antibiotic(storage, 555).ok
        until_mid = _compute_emission_protect_until(storage, now_mid)
        need_until = wave2_at + timedelta(
            minutes=EMISSION_WAVE_GAP_MINUTES + EMISSION_PROTECT_BUFFER_MINUTES
        )
        assert until_mid >= need_until - timedelta(seconds=2)
        assert has_emission_protection(storage, 555, wave2_at + timedelta(minutes=EMISSION_WAVE_GAP_MINUTES))

        # Скидка техника.
        discounted, pct = apply_tech_repair_discount(storage, 111, 1000)
        assert pct == 8 and discounted == 920

        from app.game_logic import (
            default_trader_sell_catalog_buttons,
            list_owned_trader_sell_buttons,
            canonical_sell_item_key,
        )

        assert canonical_sell_item_key("armor_sunrise") == canonical_sell_item_key("armor_zarya")
        assert canonical_sell_item_key("armor_bulat") == canonical_sell_item_key("armor_berill5m")

        storage.change_money(111, 600_000)
        assert buy_item(storage, 111, "armor_zarya").ok
        assert buy_item(storage, 111, "armor_exo").ok
        player = storage.get_character(111, refresh_energy=False)
        assert player is not None
        for category in ("consumables", "trophies", "gear", "armor", "weapons"):
            owned = list_owned_trader_sell_buttons(player, category)
            owned_callbacks = [cb for _, cb in owned]
            assert len(owned_callbacks) == len(set(owned_callbacks)), (
                f"duplicate owned sell buttons in {category}: {owned_callbacks}"
            )
            fallback = default_trader_sell_catalog_buttons(category)
            fallback_callbacks = [cb for _, cb in fallback]
            assert len(fallback_callbacks) == len(set(fallback_callbacks)), (
                f"duplicate catalog sell buttons in {category}: {fallback_callbacks}"
            )

        # Artifact hunt mini-game (visual field).
        from app.artifact_hunt import (
            abandon_artifact_hunt,
            artifact_beside_anomaly,
            get_hunt_session,
            location_anomaly_count,
            move_artifact_hunt,
            start_artifact_hunt,
        )

        storage.restore_energy(111, 100)
        storage.set_location(111, "Кордон")
        if int(storage.get_character(111, refresh_energy=False).inventory.get("detector_otklik", 0)) <= 0:
            assert buy_item(storage, 111, "detector_otklik").ok
        hunt = start_artifact_hunt(storage, 111)
        assert hunt.ok, hunt.text
        assert hunt.payload and hunt.payload.get("hunt_image")
        from app.stash_hunt import start_stash_hunt

        blocked_stash = start_stash_hunt(storage, 111, source="found")
        assert not blocked_stash.ok, blocked_stash.text
        assert "охот" in blocked_stash.text.lower()
        resume_hunt = start_artifact_hunt(storage, 111)
        assert resume_hunt.ok, resume_hunt.text
        assert not (resume_hunt.payload or {}).get("hunt_started")
        sess = get_hunt_session(storage, 111)
        assert sess is not None
        assert artifact_beside_anomaly(sess), "artifact must spawn next to an anomaly"
        assert location_anomaly_count("Кордон") == 8
        assert location_anomaly_count("Радар") == 20
        assert len(sess.anomalies) == location_anomaly_count("Кордон")
        step = move_artifact_hunt(storage, 111, "right")
        assert step.payload is not None
        if get_hunt_session(storage, 111) is not None:
            left = abandon_artifact_hunt(storage, 111)
            assert left.ok, left.text
            assert get_hunt_session(storage, 111) is None
        else:
            assert step.payload.get("hunt_done") or step.payload.get("hunt_dead")

        # Координаты хабара во время охоты не должны блокировать игрока после вылазки.
        from unittest.mock import patch

        from app.player_busy import player_busy_reason
        from app.stash_hunt import (
            STASH_COORDINATE_KEY,
            clear_stash_session,
            get_stash_session,
            start_stash_hunt,
            try_random_stash_coordinates,
        )

        storage.restore_energy(111, 100)
        _ch_hp = storage.get_character(111, refresh_energy=False)
        if _ch_hp is not None and _ch_hp.health <= 0:
            from app.game_logic import effective_max_health

            storage.change_health(111, int(effective_max_health(_ch_hp)) - _ch_hp.health)
        assert start_artifact_hunt(storage, 111).ok
        with patch("app.stash_hunt.random.random", return_value=0.0):
            assert try_random_stash_coordinates(storage, 111)
        assert get_stash_session(storage, 111) is None
        assert int(storage.get_character(111, refresh_energy=False).inventory.get(STASH_COORDINATE_KEY, 0)) >= 1
        assert abandon_artifact_hunt(storage, 111).ok
        assert get_hunt_session(storage, 111) is None
        assert get_stash_session(storage, 111) is None
        assert player_busy_reason(storage, 111) is None
        stash_start = start_stash_hunt(storage, 111, source="found")
        assert stash_start.ok, stash_start.text
        assert get_stash_session(storage, 111) is not None
        clear_stash_session(storage, 111)

        ch = storage.get_character(111, refresh_energy=False)
        assert ch is not None
        if ch.health <= 0:
            from app.game_logic import effective_max_health

            max_hp = int(effective_max_health(ch))
            storage.change_health(111, max_hp - ch.health, max_health=max_hp)

        from app.game_logic import (
            DAILY_ARTIFACT_HUNT_BONUS_RU,
            daily_artifact_hunt_done_today,
            equip_armor,
            equip_artifact,
            has_extra_artifact_slot,
            max_artifact_slots,
            mark_daily_artifact_hunt_done,
            unequip_artifact,
        )

        # Стартовая куртка T1 — без ячеек артов.
        assert max_artifact_slots(storage.get_character(111, refresh_energy=False)) == 0
        assert not has_extra_artifact_slot(storage.get_character(111, refresh_energy=False))
        storage.change_money(111, 200_000)
        set_vendor_tier(storage, 111, "tech", 2)
        slot_buy = buy_item(storage, 111, "artifact_slot")
        assert slot_buy.ok, slot_buy.text
        assert has_extra_artifact_slot(storage.get_character(111, refresh_energy=False))
        # T1 броня + апгрейд техника = 1 ячейка.
        assert max_artifact_slots(storage.get_character(111, refresh_energy=False)) == 1
        slot_again = buy_item(storage, 111, "artifact_slot")
        assert not slot_again.ok

        # T3 броня → 1 база + апгрейд = 2; T4 → 2+1 = 3.
        storage.add_item(111, "armor_seva", 1)
        assert equip_armor(storage, 111, "armor_seva").ok
        assert max_artifact_slots(storage.get_character(111, refresh_energy=False)) == 2
        storage.add_item(111, "armor_exo", 1)
        assert equip_armor(storage, 111, "armor_exo").ok
        assert max_artifact_slots(storage.get_character(111, refresh_energy=False)) == 3

        storage.add_item(111, "artifact", 1)
        storage.add_item(111, "artifact_power", 1)
        storage.add_item(111, "artifact_vitality", 1)
        eq1 = equip_artifact(storage, 111, "artifact")
        eq2 = equip_artifact(storage, 111, "artifact_power")
        eq3 = equip_artifact(storage, 111, "artifact_vitality")
        assert eq1.ok, eq1.text
        assert eq2.ok, eq2.text
        assert eq3.ok, eq3.text
        equipped = storage.get_character(111, refresh_energy=False)
        assert equipped.equipment.get("artifact") not in {"", "Нет", None}
        assert equipped.equipment.get("artifact_2") not in {"", "Нет", None}
        assert equipped.equipment.get("artifact_3") not in {"", "Нет", None}
        from app.game_logic import (
            format_inventory,
            player_owns_sellable_item,
            record_valuable_artifact_found_stat,
            sell_item,
        )

        assert player_owns_sellable_item(equipped, "artifact_power")
        sell_slot2 = sell_item(storage, 111, "artifact_power")
        assert sell_slot2.ok, sell_slot2.text
        after_sell = storage.get_character(111, refresh_energy=False)
        assert after_sell.equipment.get("artifact_2") in {"", "Нет", None}
        storage.add_item(111, "artifact_power", 1)
        cooldown_res = equip_artifact(storage, 111, "artifact_power")
        assert not cooldown_res.ok
        storage.delete_meta("artifact_equip_cd:111:artifact_power")
        assert equip_artifact(storage, 111, "artifact_power").ok
        inv_text = format_inventory(storage.get_character(111, refresh_energy=False), storage=storage)
        assert "Артефакт 2:" in inv_text
        assert "Артефакт 3:" in inv_text
        junk_before = storage.get_player_stats(111)["artifacts_found"]
        record_valuable_artifact_found_stat(storage, 111, "artifact_junk_bolt")
        assert storage.get_player_stats(111)["artifacts_found"] == junk_before
        record_valuable_artifact_found_stat(storage, 111, "artifact_power")
        assert storage.get_player_stats(111)["artifacts_found"] == junk_before + 1
        from app.game_logic import build_artifact_find_log_text, on_artifact_found

        on_artifact_found(storage, 111, "artifact_junk_bolt", location="Болото", source="hunt", detector_name="Отклик")
        on_artifact_found(storage, 111, "artifact_power", location="Радар", source="quest")
        log_text = build_artifact_find_log_text(storage, 111)
        assert "Журнал находок" in log_text
        assert "Болото" in log_text and "Отклик" in log_text
        assert "Радар" in log_text and "Контракт" in log_text
        assert "мусор" in log_text
        from app.bot import WAREHOUSE_CUSTOM_ITEM_KEYS

        assert WAREHOUSE_CUSTOM_ITEM_KEYS >= frozenset(
            {"artifact", "artifact_power", "artifact_vitality", "artifact_antirad"}
        )
        # Даунгрейд брони снимает лишние арты.
        storage.add_item(111, "armor_leather", 1)
        down = equip_armor(storage, 111, "armor_leather")
        assert down.ok, down.text
        after_down = storage.get_character(111, refresh_energy=False)
        assert max_artifact_slots(after_down) == 1
        filled = sum(
            1
            for k in ("artifact", "artifact_2", "artifact_3")
            if str(after_down.equipment.get(k, "Нет") or "Нет") not in ("", "Нет")
        )
        assert filled == 1
        # Вернём T4 для дальнейших тестов и снимем арты.
        storage.add_item(111, "armor_exo", 1)
        assert equip_armor(storage, 111, "armor_exo").ok
        while True:
            un = unequip_artifact(storage, 111)
            if not un.ok:
                break
        after_un = storage.get_character(111, refresh_energy=False)
        assert after_un.equipment.get("artifact") in {"", "Нет", None}
        assert after_un.equipment.get("artifact_2") in {"", "Нет", None}
        assert after_un.equipment.get("artifact_3") in {"", "Нет", None}
        # Вернём базовую броню — дальше smoke проверяет апгрейды поверх её смягчения.
        storage.set_equipment_item(111, "armor", "Куртка новичка")
        storage.update_equipment_fields(111, {"armor_upgrade_level": 0})

        assert not daily_artifact_hunt_done_today(storage, 111)
        mark_daily_artifact_hunt_done(storage, 111)
        assert daily_artifact_hunt_done_today(storage, 111)
        assert DAILY_ARTIFACT_HUNT_BONUS_RU == 1000

        from app.mini_events import (
            HELP_EVENT_NEXT_META,
            get_active_help_event,
            help_event_is_joinable,
            join_help_event,
            process_help_event_cycle,
            thanks_text,
        )

        storage.set_meta(HELP_EVENT_NEXT_META, datetime.now(timezone.utc).isoformat())
        radio = process_help_event_cycle(storage)
        assert radio is not None and radio.get("kind") == "call"
        event = get_active_help_event(storage)
        assert event is not None
        assert help_event_is_joinable(storage, 111)
        idle_join = join_help_event(storage, 111)
        # Отклик по рации — полевая вылазка: нужно сначала доехать до локации события.
        assert not idle_join.ok
        assert "локаци" in idle_join.text.lower()
        thanks = thanks_text({"helper_names": ["Старый"], "helper_factions": ["Долг"], "thanks_speaker": "Группа учёных"})
        assert "Старый" in thanks and "Долг" in thanks

        from app.special_events import (
            SPECIAL_EVENT_NEXT_META,
            complete_special_event_objective,
            get_active_special_event,
            get_shop_stock,
            join_special_event,
            process_special_event_cycle,
            start_special_event,
            travel_blocked_by_special_event,
        )
        from app.quest_mission import clear_mission_session

        def _clear_mission() -> None:
            clear_mission_session(storage, 111)
            storage.set_active_contract(111, None)

        storage.set_meta(SPECIAL_EVENT_NEXT_META, datetime.now(timezone.utc).isoformat())
        # Форсируем виды событий по одному.
        storm = start_special_event(storage, kind="anomaly_storm")
        assert storm["kind"] == "anomaly_storm"
        assert travel_blocked_by_special_event(
            storage, from_location="Кордон", to_location="Свалка"
        )
        storage.set_location(111, "Кордон")
        storm_join = join_special_event(storage, 111)
        assert storm_join.ok, storm_join.text
        storm_done = complete_special_event_objective(
            storage, 111, title="Поиск прохода в шторме"
        )
        assert storm_done and "Проход" in storm_done
        assert travel_blocked_by_special_event(
            storage, from_location="Кордон", to_location="Свалка"
        ) is None
        _clear_mission()

        bandits = start_special_event(storage, kind="bandit_blockade")
        assert get_shop_stock(storage) is not None
        assert get_shop_stock(storage).get("vodka", 0) == 2
        loc_b = str(bandits["location"])
        storage.set_location(111, loc_b)
        # Уйти с локации нельзя.
        assert travel_blocked_by_special_event(
            storage, from_location=loc_b, to_location="Кордон"
        )
        # Прийти на штурм можно.
        assert travel_blocked_by_special_event(
            storage, from_location="Кордон", to_location=loc_b
        ) is None
        dens = int(bandits["dens_left"])
        for i in range(dens):
            note = complete_special_event_objective(
                storage, 111, title=f"Логово бандитов: {loc_b}"
            )
            assert note
        assert get_shop_stock(storage) is None
        _clear_mission()

        heli = start_special_event(storage, kind="heli_crash")
        storage.set_location(111, str(heli["location"]))
        storage.add_item(111, "ammo_pistol", 5)
        storage.add_item(111, "ammo_shotgun", 5)
        storage.add_item(111, "ammo_rifle", 5)
        storage.add_item(111, "ammo_gauss", 5)
        heli_join = join_special_event(storage, 111)
        assert heli_join.ok, heli_join.text
        from app.quest_mission import get_mission_session as _get_heli_session

        heli_sess = _get_heli_session(storage, 111)
        assert heli_sess is not None
        assert heli_sess.kind == "clear_marauder"
        assert not heli_sess.enemies, "вертушка не должна спавнить мутантов"
        assert heli_sess.npcs, "вертушка должна спавнить военных"
        assert all(k == "soldier" for k in heli_sess.npc_kinds)
        heli_loot = complete_special_event_objective(
            storage, 111, title=f"Обломки вертушки: {heli['location']}"
        )
        assert heli_loot and "800" in heli_loot
        _clear_mission()

        dark = start_special_event(storage, kind="dark_stalker")
        storage.set_location(111, str(dark["location"]))
        storage.add_item(111, "ammo_pistol", 5)
        storage.add_item(111, "ammo_shotgun", 5)
        storage.add_item(111, "ammo_rifle", 5)
        storage.add_item(111, "ammo_gauss", 5)
        dark_join = join_special_event(storage, 111)
        assert dark_join.ok, dark_join.text
        _clear_mission()

        # Волна 2: пленник / Гигант / колонна Монолита.
        assert "Припять" in {loc["name"] for loc in storage.get_locations()}
        storage.restore_energy(111, 100)
        rescue = start_special_event(storage, kind="monolith_rescue")
        assert rescue["kind"] == "monolith_rescue" and rescue["location"] == "Припять"
        storage.set_location(111, "Припять")
        storage.add_item(111, "ammo_pistol", 5)
        storage.add_item(111, "ammo_shotgun", 5)
        storage.add_item(111, "ammo_rifle", 5)
        storage.add_item(111, "ammo_gauss", 5)
        storage.add_item(111, "medkit", 2)
        rescue_join = join_special_event(storage, 111)
        assert rescue_join.ok, rescue_join.text
        rescue_note = complete_special_event_objective(
            storage, 111, title="Спасение пленного: Припять"
        )
        assert rescue_note and "Пленник" in rescue_note
        _clear_mission()

        storage.restore_energy(111, 100)
        giant = start_special_event(storage, kind="giant")
        assert giant["kind"] == "giant"
        assert int(giant["boss_hp"]) == 100
        storage.set_location(111, str(giant["location"]))
        storage.add_item(111, "ammo_pistol", 5)
        storage.add_item(111, "ammo_shotgun", 5)
        storage.add_item(111, "ammo_rifle", 5)
        storage.add_item(111, "ammo_gauss", 5)
        storage.add_item(111, "medkit", 2)
        giant_join = join_special_event(storage, 111)
        assert giant_join.ok, giant_join.text
        assert "Гигант" in giant_join.text
        from app.quest_mission import get_mission_session, _finish_success

        giant_session = get_mission_session(storage, 111)
        assert giant_session is not None
        assert "giant" in (giant_session.enemy_kinds or [])
        from app.mutant_assets import load_mutant_card_sprite, load_mutant_grid_sprite, special_event_call_photo

        assert load_mutant_grid_sprite("giant") is not None
        assert load_mutant_card_sprite("giant") is not None
        assert special_event_call_photo("giant") is not None
        # Эфемерные ключи special-event должны проходить finish (не «Контракт повреждён»).
        giant_finish = _finish_success(storage, 111, giant_session)
        assert giant_finish.ok, giant_finish.text
        active_giant = get_active_special_event(storage)
        assert active_giant is not None
        assert int(active_giant["boss_hp"]) < 100
        from app.special_events import special_events_status_line

        assert "прочность" in special_events_status_line(storage)
        assert "псевдогигант" in str(giant.get("call_text") or "").lower()
        assert "нескольких смертей" not in str(giant.get("call_text") or "")
        assert "HP" not in str(giant.get("call_text") or "")
        _clear_mission()

        storage.restore_energy(111, 100)
        march = start_special_event(storage, kind="monolith_march")
        assert march["kind"] == "monolith_march"
        assert march["location"] == "Радар"
        assert march.get("target_base")
        storage.set_location(111, "Радар")
        storage.add_item(111, "ammo_pistol", 5)
        storage.add_item(111, "ammo_shotgun", 5)
        storage.add_item(111, "ammo_rifle", 5)
        storage.add_item(111, "ammo_gauss", 5)
        storage.add_item(111, "medkit", 2)
        march_join = join_special_event(storage, 111)
        assert march_join.ok, march_join.text
        need = int(march["hits_needed"])
        for _ in range(need):
            note = complete_special_event_objective(
                storage, 111, title="Колонна Монолита: перехват"
            )
            assert note
        done_march = get_active_special_event(storage)
        assert done_march is None or done_march.get("resolved")
        _clear_mission()

        # Закрытая ГП Монолит + база ЧАЭС + окно боя 90/10.
        from app.monolith_war import (
            MONOLITH_BASE,
            MONOLITH_FACTION,
            begin_monolith_war_window,
            filter_travel_locations_for_faction,
            force_start_monolith_war,
            get_pending_monolith_war,
            join_monolith_war,
            resolve_pending_monolith_war,
            save_pending_monolith_war,
            send_monolith_bots,
        )
        from app.game_logic import admin_set_player_faction, FACTION_HOME_BASE

        assert MONOLITH_BASE in {loc["name"] for loc in storage.get_locations()}
        assert FACTION_HOME_BASE[MONOLITH_FACTION] == MONOLITH_BASE
        chaes = storage.get_location(MONOLITH_BASE)
        assert chaes is not None and chaes.get("controlled_by") == MONOLITH_FACTION
        blocked = travel_to(storage, 111, MONOLITH_BASE)
        assert not blocked.ok
        visible = filter_travel_locations_for_faction(storage.get_locations(), "Долг")
        assert MONOLITH_BASE not in {loc["name"] for loc in visible}
        admin_set_player_faction(storage, target="111", faction=MONOLITH_FACTION)
        mono = storage.get_character(111, refresh_energy=False)
        assert mono is not None and mono.faction == MONOLITH_FACTION
        assert mono.location == MONOLITH_BASE
        ok_home = travel_to(storage, 222, MONOLITH_BASE)
        assert not ok_home.ok
        wid = storage.create_war_lobby("Долг", MONOLITH_BASE, 222)
        storage.restore_energy(222, 100)
        pending = begin_monolith_war_window(
            storage,
            war_id=wid,
            location_name=MONOLITH_BASE,
            host_faction="Долг",
            attacker_ids=[222],
            mode="defend",
            energy_spent_ids=[222],
        )
        join_m = join_monolith_war(storage, 111)
        assert join_m.ok, join_m.text
        assert "Начать бой сейчас" in join_m.text
        bots = send_monolith_bots(storage, 111)
        assert bots.ok, bots.text
        # Досрочный старт без людей → процентный исход (не оставляем cwar-сессию).
        pending = get_pending_monolith_war(storage) or dict(pending)
        pending = dict(pending)
        pending["monolith_ids"] = []
        pending["monolith_names"] = []
        pending["bots_sent"] = True
        pending["bot_count"] = 3
        save_pending_monolith_war(storage, pending)
        early = force_start_monolith_war(storage, 111)
        assert early.ok, early.text
        assert ((early.payload or {}).get("monolith_outcome") or {}).get("kind") == "percent"
        storage.set_meta("monolith_war:pending", "")
        # Вернём 111 в Долг для дальнейших smoke-тестов.
        admin_set_player_faction(storage, target="111", faction="Долг")
        storage.set_faction_leader("Долг", 111)

        # Кнопка/команда атаки Монолита.
        from app.monolith_war import start_monolith_attack
        from app.faction_bots import upgrade_faction_bots as upgrade_mono_bots
        from app.keyboards import faction_group_keyboard, faction_upgrades_keyboard, war_lobby_keyboard

        storage.create_character(444, "MonoLead", "Мужской")
        admin_set_player_faction(storage, target="444", faction=MONOLITH_FACTION)
        storage.set_faction_leader(MONOLITH_FACTION, 444)
        storage.restore_energy(444, 100)
        mono_tier = upgrade_mono_bots(storage, 444)
        assert not mono_tier.ok
        assert "элитном" in mono_tier.text.lower() or "тир" in mono_tier.text.lower()
        mono_kb = faction_group_keyboard(is_leader=True, faction=MONOLITH_FACTION)
        mono_cbs = {
            btn.callback_data
            for row in mono_kb.inline_keyboard
            for btn in row
            if btn.callback_data
        }
        assert "faction:upgrades:menu" in mono_cbs
        mono_up_kb = faction_upgrades_keyboard(faction=MONOLITH_FACTION)
        mono_up_cbs = {
            btn.callback_data
            for row in mono_up_kb.inline_keyboard
            for btn in row
            if btn.callback_data
        }
        assert "faction:bots:upgrade" not in mono_up_cbs
        assert "faction:bots:count" in mono_up_cbs
        lobby_kb = war_lobby_keyboard([], monolith_join=True)
        lobby_cbs = {
            btn.callback_data
            for row in lobby_kb.inline_keyboard
            for btn in row
            if btn.callback_data
        }
        assert "monolith_war:start" in lobby_cbs
        from app.keyboards import cwar_grid_keyboard

        cwar_kb = cwar_grid_keyboard(is_active_turn=False, medkit_available=False)
        cwar_cbs = {
            btn.callback_data
            for row in cwar_kb.inline_keyboard
            for btn in row
            if btn.callback_data
        }
        assert "cwar:forfeit" in cwar_cbs
        assert cwar_kb.inline_keyboard[0][0].callback_data == "cwar:forfeit"
        cwar_active_kb = cwar_grid_keyboard(is_active_turn=True, medkit_available=True)
        assert cwar_active_kb.inline_keyboard[0][0].callback_data == "cwar:forfeit"
        atk = start_monolith_attack(storage, 444, "Свалка")
        assert atk.ok, atk.text
        from app.monolith_war import (
            format_monolith_war_call,
            get_pending_monolith_war,
            monolith_join_button_label,
            resolve_pending_monolith_war,
        )
        from app.clan_war_grid import clear_cwar_session, get_cwar_session_by_player

        pending_after_atk = get_pending_monolith_war(storage)
        assert pending_after_atk is not None
        assert pending_after_atk.get("mode") == "attack"
        assert 444 in pending_after_atk.get("monolith_ids", [])
        assert monolith_join_button_label(storage) == "☢ Вступить в атаку Монолита"
        forced_atk = resolve_pending_monolith_war(storage, force=True)
        assert forced_atk is not None and forced_atk.get("kind") == "tactical"
        sess_atk = forced_atk.get("session")
        assert sess_atk is not None and not sess_atk.monolith_defense
        assert 444 in sess_atk.player_ids
        if sess_atk is not None:
            clear_cwar_session(storage, sess_atk)
        storage.set_meta("monolith_war:pending", "")

        atk = start_monolith_attack(storage, 444, "Болото")
        assert atk.ok, atk.text
        from app.monolith_war import format_monolith_war_call

        attack_call = format_monolith_war_call(
            {"location": "Болото", "mode": "attack", "host_faction": MONOLITH_FACTION}
        )
        assert "Внимание" in attack_call
        assert "разведки" in attack_call
        assert "подключение бойцов" not in attack_call
        assert "Окно" not in attack_call
        assert "90/10" not in attack_call
        defend_call = format_monolith_war_call(
            {"location": MONOLITH_BASE, "mode": "defend", "host_faction": "Долг"}
        )
        assert "Штурм базы Монолита" in defend_call
        # Корректно закрываем окно/лобби, не оставляя war_lobbies in_progress.
        forced = resolve_pending_monolith_war(storage, force=True)
        assert forced is not None
        storage.set_meta("monolith_war:pending", "")

        # Марш Монолита не целится в ЧАЭС.
        from app.special_events import MARCH_TARGET_BASES

        assert "ЧАЭС" not in MARCH_TARGET_BASES
        assert "Росток" in MARCH_TARGET_BASES

        # Сброс особого события, чтобы не мешать дальнейшим smoke-переходам.
        storage.set_meta("special_event:active", "")
        storage.set_meta("shop:stock:consumables", "")
        storage.restore_energy(111, 100)
        storage.restore_energy(222, 100)
        storage.restore_energy(333, 100)

        from app.mini_events import complete_help_event_if_helper
        from app.game_logic import ACHIEVEMENT_BY_KEY, _progress_and_unlock_achievements

        assert ACHIEVEMENT_BY_KEY["radio_help_50"].title == "Рука помощи"
        help_event = dict(event)
        help_event["helpers"] = [111]
        help_event["done_helpers"] = []
        storage.set_meta("help_event:active", __import__("json").dumps(help_event, ensure_ascii=False))
        assert complete_help_event_if_helper(storage, 111) == "Помощь по рации засчитана."
        assert storage.get_player_stats(111)["radio_helps"] == 1
        storage.add_player_stat(111, "radio_helps", 49)
        assert storage.get_player_stats(111)["radio_helps"] == 50
        radio_ach = _progress_and_unlock_achievements(storage, 111)
        assert "Рука помощи" in radio_ach
        assert "radio_help_50" in storage.get_player_achievement_keys(111)

        from app.combat_loot import grant_combat_loot

        loot_note = grant_combat_loot(storage, 111, npc=False)
        assert loot_note is None or isinstance(loot_note, str)

        from app.coop_mission import CoopMissionSession, coop_shoot_available

        shoot_sess = CoopMissionSession(
            session_id="shoot-t",
            lobby_id="sl",
            location="Кордон",
            player_ids=[111],
            enemies=[(2, 2)],
            enemy_hp=[12],
            enemy_kinds=["blind_dog"],
        )
        assert coop_shoot_available(shoot_sess)

        travel_result = travel_to(storage, 111, "Янтарь")
        assert travel_result.ok, travel_result.text
        in_transit = storage.get_character(111, refresh_energy=False)
        assert in_transit is not None
        from app.game_logic import (
            is_traveling,
            accept_quest_contract,
            run_contract_work,
            collect_travel_eta_notices,
            travel_status_text,
        )

        assert is_traveling(in_transit)
        status = travel_status_text(in_transit)
        assert status is not None and "Осталось ехать" in status
        eta_notices = collect_travel_eta_notices(storage)
        assert any(uid == 111 and "Осталось ехать" in text for uid, text in eta_notices)
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with storage._connect() as conn:
            conn.execute(
                "UPDATE characters SET travel_arrives_at = ? WHERE telegram_id = ?",
                (past, 111),
            )
        storage.resolve_travel_if_due(111)
        after_travel = storage.get_character(111, refresh_energy=False)
        assert after_travel is not None
        assert after_travel.location == "Янтарь"
        assert not is_traveling(after_travel)
        assert after_travel.truck_durability < before_truck_durability
        no_fuel_player = storage.get_character(111, refresh_energy=False)
        assert no_fuel_player is not None
        if no_fuel_player.diesel > 0:
            storage.change_diesel(111, -no_fuel_player.diesel)
        if no_fuel_player.gasoline > 0:
            storage.change_gasoline(111, -no_fuel_player.gasoline)
        bound_foot = travel_to(storage, 111, "Болото")
        assert not bound_foot.ok
        assert "грузовик" in bound_foot.text.lower() or "пешком" in bound_foot.text.lower()
        from app.game_logic import garage_deposit_truck, garage_withdraw_truck

        repaired_before = repair_truck(storage, 111)
        assert repaired_before.ok, repaired_before.text
        deposited = garage_deposit_truck(storage, 111)
        assert deposited.ok, deposited.text
        assert "без аренды" in deposited.text.lower() or "обратно" in deposited.text.lower()
        foot_travel = travel_to(storage, 111, "Болото")
        assert foot_travel.ok, foot_travel.text
        assert "пешком" in foot_travel.text.lower()
        assert can_travel_by_truck(storage.get_character(111, refresh_energy=False)) is False
        past2 = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with storage._connect() as conn:
            conn.execute(
                "UPDATE characters SET travel_arrives_at = ? WHERE telegram_id = ?",
                (past2, 111),
            )
        storage.resolve_travel_if_due(111)
        storage.change_diesel(111, 10)
        storage.change_money(111, 5000)
        withdrawn = garage_withdraw_truck(storage, 111)
        assert withdrawn.ok, withdrawn.text
        assert "без аренды" in withdrawn.text.lower() or "твоя техника" in withdrawn.text.lower()
        from app.game_logic import GARAGE_VEHICLE_RENTALS_META
        import json as _json

        rentals_raw = storage.get_meta(GARAGE_VEHICLE_RENTALS_META) or "[]"
        assert not any(
            int(e.get("player_id") or 0) == 111 and e.get("vehicle_key") == "truck"
            for e in _json.loads(rentals_raw)
        )
        after_repair = storage.get_character(111, refresh_energy=False)
        assert after_repair is not None
        assert after_repair.truck_durability == 100
        assert after_repair.truck_owned

        storage.change_money(111, 10_000)
        upgraded = upgrade_armor(storage, 111)
        assert upgraded.ok, upgraded.text
        assert int(storage.get_character(111, refresh_energy=False).inventory.get("armor_upgrade", 0)) == 1
        assert armor_defense(storage.get_character(111, refresh_energy=False)) == 0
        from app.game_logic import install_armor_upgrade, unequip_armor_upgrade, equip_armor

        installed = install_armor_upgrade(storage, 111)
        assert installed.ok, installed.text
        assert armor_defense(storage.get_character(111, refresh_energy=False)) == 1
        player_111 = storage.get_character(111, refresh_energy=False)
        # Нижняя граница урона поднята на +10 (см. apply_incoming_damage).
        assert apply_incoming_damage(10, player_111) == 11
        upgraded2 = upgrade_armor(storage, 111)
        assert upgraded2.ok, upgraded2.text
        installed2 = install_armor_upgrade(storage, 111)
        assert installed2.ok, installed2.text
        assert armor_defense(storage.get_character(111, refresh_energy=False)) == 2
        removed = unequip_armor_upgrade(storage, 111)
        assert removed.ok, removed.text
        assert armor_defense(storage.get_character(111, refresh_energy=False)) == 1
        assert int(storage.get_character(111, refresh_energy=False).inventory.get("armor_upgrade", 0)) == 1

        storage.set_location(111, "Росток")
        contract = accept_quest_contract(storage, 111, "easy_boloto")
        assert contract.ok, contract.text
        from app.session_recovery import try_auto_recover_orphan_contract
        from app.player_busy import player_busy_reason

        assert try_auto_recover_orphan_contract(storage, 111) is None
        assert storage.get_active_contract(111) is not None
        assert player_busy_reason(storage, 111) is None
        storage.set_location(111, "Болото")
        work = run_contract_work(storage, 111)
        assert work.ok, work.text
        assert (work.payload or {}).get("mission_active"), work.text
        assert (work.payload or {}).get("mission_image"), "mission png missing"

        from app.quest_mission import (
            get_mission_session,
            save_mission_session,
            clear_mission_session,
            _finish_success,
            move_quest_mission,
            render_mission_frame,
            use_mission_medkit,
        )

        # Army medkit must heal during mission (not only basic medkit).
        storage.add_item(111, "medkit_army", 1)
        # Remove basic medkits so only army is available.
        cur_inv = storage.get_character(111, refresh_energy=False)
        basic = int(cur_inv.inventory.get("medkit", 0))
        if basic > 0:
            storage.remove_item(111, "medkit", basic)
        storage.change_health(111, -40)
        med_result = use_mission_medkit(storage, 111)
        assert med_result.ok, med_result.text
        assert int(storage.get_character(111, refresh_energy=False).inventory.get("medkit_army", 0)) == 0

        session = get_mission_session(storage, 111)
        assert session is not None
        from app.quest_mission import _save_mission_if_turn_ok

        hp_before_stale = storage.get_character(111, refresh_energy=False).health
        stale_seq = session.turn_seq
        session.turn_seq = stale_seq + 1
        save_mission_session(storage, 111, session)
        session = get_mission_session(storage, 111)
        assert session is not None
        session.turn_seq = stale_seq + 2
        assert not _save_mission_if_turn_ok(storage, 111, session, stale_seq)
        assert storage.get_character(111, refresh_energy=False).health == hp_before_stale
        assert get_mission_session(storage, 111) is not None

        session = get_mission_session(storage, 111)
        assert session is not None
        assert session.kind == "collect"
        assert len(session.objectives) >= 1
        # Easy: только аномалии, без мутантов/НПС.
        assert session.difficulty == "easy"
        assert len(session.hazards) >= 1
        assert len(session.enemies) == 0
        assert len(session.npcs) == 0
        png = render_mission_frame(session, storage.get_character(111, refresh_energy=False))
        assert len(png) > 1000

        # Hard: аномалии + мутанты.
        from app.quest_mission import _build_session, _difficulty_threat_flags, _maybe_move_hostiles
        from app.game_logic import QUEST_CONTRACTS, QUESTS

        hard_tpl = QUEST_CONTRACTS["hard_forest"]
        hard_sess = _build_session(hard_tpl, QUESTS["hard"])
        assert _difficulty_threat_flags("hard", hard_tpl.mission_kind) == (True, True, False)
        assert len(hard_sess.hazards) >= 1
        assert len(hard_sess.enemies) >= 1
        assert len(hard_sess.npcs) == 0

        # Heavy: аномалии + НПС.
        heavy_tpl = QUEST_CONTRACTS["heavy_valley"]
        heavy_sess = _build_session(heavy_tpl, QUESTS["heavy"])
        assert heavy_tpl.mission_kind == "clear_marauder"
        assert len(heavy_sess.hazards) >= 1
        assert len(heavy_sess.npcs) >= 1

        from app.game_logic import QuestContractTemplate
        from app.quest_mission import _apply_special_mission_spawn_overrides

        march_tpl = QuestContractTemplate(
            key="march_test",
            difficulty="hard",
            title="Колонна Монолита: перехват",
            work_location="Радар",
            return_home=False,
            mission_kind="clear_marauder",
        )
        march_sess = _build_session(march_tpl, QUESTS["hard"])
        player111 = storage.get_character(111, refresh_energy=False)
        assert player111 is not None
        _apply_special_mission_spawn_overrides(march_tpl, march_sess, player111)
        assert len(march_sess.npcs) >= 4
        assert all(kind == "monolith" for kind in march_sess.npc_kinds)
        from app.npc_assets import MONOLITH_AVATAR_SOURCE, load_npc_grid_sprite

        assert MONOLITH_AVATAR_SOURCE.is_file()
        monolith_sprite = load_npc_grid_sprite("monolith")
        assert monolith_sprite is not None and len(monolith_sprite) > 500
        assert all(
            weapon in ("Гаусс-пушка", "Винтарь ВС", "АН-94", "ГП37")
            for weapon in march_sess.npc_weapons
        )
        assert len(march_sess.enemies) == 0

        dark_tpl = QuestContractTemplate(
            key="dark_test",
            difficulty="hard",
            title="Дуэль: Тёмный сталкер (Янтарь)",
            work_location="Янтарь",
            return_home=False,
            mission_kind="clear_marauder",
        )
        dark_sess = _build_session(dark_tpl, QUESTS["hard"])
        _apply_special_mission_spawn_overrides(dark_tpl, dark_sess, player111)
        assert len(dark_sess.npcs) == 1
        assert dark_sess.npc_kinds == ["dark_stalker"]
        expected_weapon = str(player111.equipment.get("weapon") or "ПМ")
        if expected_weapon in {"", "Нож"}:
            expected_weapon = "ПМ"
        assert dark_sess.npc_weapons == [expected_weapon]
        assert len(dark_sess.enemies) == 0

        from app.mutant_abilities import mutant_damage_multiplier

        assert mutant_damage_multiplier("tushkano", "front", ["tushkano"]) == 0.5

        from app.quest_mission import mission_shoot_available, shoot_quest_mission

        assert mission_shoot_available(heavy_sess)
        heavy_sess.player = (0, 0)
        heavy_sess.start = (5, 5)
        heavy_sess.npcs = [(1, 0), (2, 0)]
        heavy_sess.npc_kinds = ["marauder", "marauder"]
        heavy_sess.enemies = []
        heavy_sess.enemy_kinds = []
        heavy_sess.turn_seq = 0
        save_mission_session(storage, 111, heavy_sess)
        storage.add_item(111, "ammo_pistol", 20)
        storage.set_equipment_item(111, "weapon", "ПМ")
        shoot = shoot_quest_mission(storage, 111, "right")
        assert shoot.ok, shoot.text
        after_shoot = get_mission_session(storage, 111)
        assert after_shoot is not None
        # HP-бой: выстрел снимает HP из таблицы (16+18), враг не гибнет мгновенно.
        assert len(after_shoot.npcs) == 2
        assert len(after_shoot.npc_hp) == 2
        assert sum(after_shoot.npc_hp) < 34
        assert "поразил" in shoot.text.lower()

        # clear_mutant (Зачистка Радара): стрельба по мутантам.
        radar_tpl = QUEST_CONTRACTS["impossible_radar"]
        radar_sess = _build_session(radar_tpl, QUESTS["impossible"])
        assert radar_tpl.mission_kind == "clear_mutant"
        assert mission_shoot_available(radar_sess)
        radar_sess.player = (0, 0)
        radar_sess.start = (5, 5)
        radar_sess.enemies = [(1, 0), (5, 0)]
        radar_sess.enemy_kinds = ["blind_dog", "tushkano"]
        radar_sess.npcs = []
        radar_sess.npc_kinds = []
        radar_sess.turn_seq = 0
        save_mission_session(storage, 111, radar_sess)
        mutant_shoot = shoot_quest_mission(storage, 111, "right")
        assert mutant_shoot.ok, mutant_shoot.text
        after_mutant = get_mission_session(storage, 111)
        assert after_mutant is not None
        assert len(after_mutant.enemies) == 1
        assert "поразил" in mutant_shoot.text.lower()

        # Impossible: всё вместе.
        imp_tpl = QUEST_CONTRACTS["impossible_radar"]
        imp_sess = _build_session(imp_tpl, QUESTS["impossible"])
        assert len(imp_sess.hazards) >= 1
        assert len(imp_sess.enemies) >= 1
        assert len(imp_sess.npcs) >= 1

        # Hostile move: 50% chance path doesn't crash and keeps count.
        before = len(imp_sess.enemies) + len(imp_sess.npcs)
        _maybe_move_hostiles(imp_sess)[0]
        assert len(imp_sess.enemies) + len(imp_sess.npcs) == before

        # Мутанты преследуют на любой сложности.
        from app.quest_mission import (
            _mutant_can_adjacent_attack,
            _mutants_chase_player,
            _relative_attack_side,
        )
        from app.mutant_abilities import mutant_can_melee_attack, mutant_can_ranged_attack

        hard_tpl = QUEST_CONTRACTS["hard_forest"]
        hard_chase = _build_session(hard_tpl, QUESTS["hard"])
        assert _mutants_chase_player(hard_chase)

        # 🟠/🔴: мутанты преследуют и не встают на клетку игрока.
        from app.quest_mission import _manhattan

        chase_sess = _build_session(imp_tpl, QUESTS["impossible"])
        chase_sess.player = (5, 5)
        chase_sess.enemies = [(0, 0)]
        chase_sess.enemy_kinds = ["blind_dog"]
        chase_sess.npcs = []
        chase_sess.npc_kinds = []
        chase_sess.hazards = []
        chase_sess.player_facing = "up"
        for _ in range(16):
            _maybe_move_hostiles(chase_sess)[0]
            assert chase_sess.player not in chase_sess.enemies
        assert _manhattan(chase_sess.enemies[0], chase_sess.player) == 1

        chase_sess.enemies = [(5, 6)]
        chase_sess.enemy_kinds = ["bloodsucker"]
        assert _relative_attack_side("up", (5, 5), (5, 6)) == "back"
        assert _mutant_can_adjacent_attack(chase_sess, "bloodsucker", (5, 6))
        chase_sess.enemies = [(6, 5)]
        chase_sess.enemy_kinds = ["blind_dog"]
        assert _mutant_can_adjacent_attack(chase_sess, "blind_dog", (6, 5))
        assert not _mutant_can_adjacent_attack(chase_sess, "blind_dog", (6, 6))

        chase_sess.enemies = [(5, 5)]
        chase_sess.enemy_kinds = ["tushkano"]
        chase_sess.player = (4, 4)
        assert mutant_can_melee_attack(chase_sess, "tushkano", (5, 5))
        assert not mutant_can_melee_attack(chase_sess, "tushkano", (5, 4))
        from app.mutant_abilities import _diagonal_cells, mutant_hostile_move_candidates

        assert (5, 5) in _diagonal_cells((4, 4), chase_sess.grid)
        assert (5, 4) not in mutant_hostile_move_candidates("tushkano", (4, 4), chase_sess.grid)
        chase_sess.player = (5, 5)
        chase_sess.enemies = [(5, 2)]
        chase_sess.enemy_kinds = ["burer"]
        assert mutant_can_ranged_attack(chase_sess, "burer", (5, 2))

        # Escort: anomalies always; escort follows into previous player cell; hostiles T1 or mutants.
        escort_tpl = QUEST_CONTRACTS["easy_escort_dump"]
        assert escort_tpl.mission_kind == "escort"
        boloto_escort = QUEST_CONTRACTS["easy_escort_boloto"]
        assert boloto_escort.mission_kind == "escort"
        assert boloto_escort.work_location == "Болото"
        escort_sess = _build_session(escort_tpl, QUESTS["easy"])
        assert escort_sess.escort_alive and escort_sess.escort is not None
        assert len(escort_sess.hazards) >= 1
        assert len(escort_sess.objectives) == 1
        has_mut = len(escort_sess.enemies) >= 1
        has_npc = len(escort_sess.npcs) >= 1
        assert has_mut != has_npc
        if escort_sess.npcs:
            assert all(w in {"ПМ", "Фора-12", "Обрез"} for w in escort_sess.npc_weapons)
        from app.quest_mission import move_quest_mission, clear_mission_session

        clear_mission_session(storage, 111)
        # Put escort adjacent and clear threats for a deterministic follow step.
        escort_sess.player = (2, 2)
        escort_sess.start = (2, 2)
        escort_sess.escort = (2, 3)
        escort_sess.escort_alive = True
        escort_sess.enemies = []
        escort_sess.enemy_kinds = []
        escort_sess.npcs = []
        escort_sess.npc_kinds = []
        escort_sess.npc_weapons = []
        escort_sess.hazards = []
        escort_sess.objectives = [(5, 5)]
        escort_sess.collected = []
        escort_sess.objectives_done = False
        escort_sess.turn_seq = 0
        save_mission_session(storage, 111, escort_sess)
        follow = move_quest_mission(storage, 111, "right")
        assert follow.ok, follow.text
        after_escort = get_mission_session(storage, 111)
        assert after_escort is not None
        assert after_escort.player == (3, 2)
        assert after_escort.escort == (2, 2)
        assert after_escort.escort_alive
        clear_mission_session(storage, 111)

        # Forced finish for smoke.
        session.collected = list(session.objectives)
        session.objectives_done = True
        session.player = session.start
        save_mission_session(storage, 111, session)
        done = _finish_success(storage, 111, session)
        assert done.ok, done.text
        active_after = storage.get_active_contract(111)
        assert active_after is not None  # stage return
        assert str(active_after.get("stage")) == "return"

        # Death story generator must not crash (regression: tuple in generate_death_story lines).
        from app.game_logic import build_battle_death_text, remember_death_cause, effective_max_health

        live = storage.get_character(111, refresh_energy=False)
        assert live is not None
        remember_death_cause(storage, 111, "anomaly")
        storage.change_health(111, -live.health)
        dead = storage.get_character(111, refresh_energy=False)
        assert dead is not None and dead.health <= 0
        story = build_battle_death_text(
            dead,
            where="Болото",
            cause="anomaly",
            storage=storage,
        )
        assert "аномал" in story.lower(), story[:200]
        max_hp = int(effective_max_health(dead))
        storage.change_health(111, max_hp - dead.health, max_health=max_hp)

        # Turn-in: return home → auto-complete report.
        from app.game_logic import turn_in_quest_contract, try_auto_turn_in_contract

        storage.set_location(111, faction_home_base("Долг"))
        auto = try_auto_turn_in_contract(storage, 111)
        assert auto is not None and "сдан" in auto.lower(), auto
        assert storage.get_active_contract(111) is None

        # Manual turn-in path still works if auto didn't run yet.
        storage.set_location(111, faction_home_base("Долг"))
        storage.set_active_contract(
            111,
            {"template_key": "easy_boloto", "stage": "return", "pending_reward": 1000},
        )
        manual = turn_in_quest_contract(storage, 111)
        assert manual.ok, manual.text
        assert "100" in manual.text or "+100" in manual.text  # 10% of 1000
        assert storage.get_active_contract(111) is None

        # Daily contract bonus: once per template per day, not per turn-in repeat.
        import json
        from datetime import datetime, timezone

        from app.game_logic import (
            CONTRACT_DAILY_DONE_META_PREFIX,
            DAILY_CONTRACTS_META_KEY,
            _daily_key,
        )

        today = _daily_key(datetime.now(timezone.utc))
        storage.set_meta(
            DAILY_CONTRACTS_META_KEY,
            json.dumps({"date": today, "keys": ["easy_boloto", "easy_dump"]}, ensure_ascii=False),
        )
        # Сброс claim — earlier turn-in of easy_boloto мог уже забрать бонус дня.
        storage.delete_meta(f"{CONTRACT_DAILY_DONE_META_PREFIX}111:{today}:easy_boloto")
        home_dolg = faction_home_base("Долг")
        storage.set_location(111, home_dolg)
        before_money = storage.get_character(111, refresh_energy=False).money

        storage.set_active_contract(
            111,
            {"template_key": "easy_boloto", "stage": "return", "pending_reward": 1000},
        )
        first_daily = turn_in_quest_contract(storage, 111)
        assert first_daily.ok, first_daily.text
        assert "Бонус: +500 RU" in first_daily.text
        after_first = storage.get_character(111, refresh_energy=False).money
        assert after_first >= before_money + 100 + 500  # 10% turn-in + 50% daily

        storage.set_active_contract(
            111,
            {"template_key": "easy_boloto", "stage": "return", "pending_reward": 1000},
        )
        before_repeat = storage.get_character(111, refresh_energy=False).money
        repeat_daily = turn_in_quest_contract(storage, 111)
        assert repeat_daily.ok, repeat_daily.text
        assert "Бонус: +500 RU" not in repeat_daily.text
        after_repeat = storage.get_character(111, refresh_energy=False).money
        assert after_repeat == before_repeat + 100  # only 10% turn-in

        storage.set_active_contract(
            111,
            {"template_key": "easy_dump", "stage": "return", "pending_reward": 800},
        )
        before_second = storage.get_character(111, refresh_energy=False).money
        second_daily = turn_in_quest_contract(storage, 111)
        assert second_daily.ok, second_daily.text
        assert "Бонус: +400 RU" in second_daily.text
        after_second = storage.get_character(111, refresh_energy=False).money
        assert after_second >= before_second + 80 + 400  # 10% + 50% for other daily
        storage.set_active_contract(111, None)

        # Turn-in blocked off-base.
        storage.set_active_contract(
            111,
            {"template_key": "easy_boloto", "stage": "return", "pending_reward": 500},
        )
        storage.set_location(111, "Болото")
        blocked = turn_in_quest_contract(storage, 111)
        assert not blocked.ok
        assert storage.get_active_contract(111) is not None
        storage.set_active_contract(111, None)

        # Bicycle quest mult only after bike arrival (and then consumed).
        from app.game_logic import TRANSPORT_QUEST_REWARD_MULT, build_players_faction_page_text
        from app.keyboards import players_faction_page_keyboard

        bandit_home = faction_home_base("Бандиты")
        storage.set_location(333, bandit_home)
        storage.set_last_arrival_transport(333, "bicycle")
        storage.restore_energy(333, 100)
        storage.add_item(333, "ammo_pistol", 5)
        storage.add_item(333, "ammo_rifle", 5)
        storage.add_item(333, "medkit", 5)
        bike_contract = accept_quest_contract(storage, 333, "easy_boloto")
        assert bike_contract.ok, bike_contract.text
        storage.set_location(333, "Болото")
        bike_work = run_contract_work(storage, 333)
        assert bike_work.ok, bike_work.text
        assert (bike_work.payload or {}).get("mission_active")
        bike_session = get_mission_session(storage, 333)
        assert bike_session is not None
        bike_session.collected = list(bike_session.objectives)
        bike_session.objectives_done = True
        bike_session.player = bike_session.start
        save_mission_session(storage, 333, bike_session)
        bike_done = _finish_success(storage, 333, bike_session)
        assert bike_done.ok, bike_done.text
        assert f"×{TRANSPORT_QUEST_REWARD_MULT['bicycle']:g}" in bike_done.text or "велосипед" in bike_done.text.lower()
        assert storage.get_last_arrival_transport(333) is None
        storage.set_last_arrival_transport(333, "bicycle")
        assert storage.consume_last_arrival_transport(333) == "bicycle"
        assert storage.get_last_arrival_transport(333) is None
        clear_mission_session(storage, 111)
        clear_mission_session(storage, 333)
        page_text, key, page, pages, page_players = build_players_faction_page_text(
            storage, "Долг", 0
        )
        assert "/дуэль" in page_text.lower() or "дуэль" in page_text.lower()
        kb = players_faction_page_keyboard(
            key, page=page, total_pages=pages, players=page_players, self_id=111
        )
        duel_cbs = [
            btn.callback_data
            for row in kb.inline_keyboard
            for btn in row
            if (btn.callback_data or "").startswith("duel:challenge:")
        ]
        assert len(duel_cbs) == 1
        assert duel_cbs[0].endswith(":222")

        _page_text_b, _k_b, _p_b, _pg_b, bandit_players = build_players_faction_page_text(
            storage, "Бандиты", 0
        )
        kb_bandit = players_faction_page_keyboard(
            "Бандиты", page=0, total_pages=1, players=bandit_players, self_id=111
        )
        bandit_duel_cbs = [
            btn.callback_data
            for row in kb_bandit.inline_keyboard
            for btn in row
            if (btn.callback_data or "").startswith("duel:challenge:")
        ]
        assert len(bandit_duel_cbs) == 1
        assert bandit_duel_cbs[0].endswith(":333")

        # Bandits cannot take home-location easy dump contract.
        from app.game_logic import list_quest_contracts_for_character, list_available_travel_modes

        bandit = storage.get_character(333, refresh_energy=False)
        assert all(t.work_location != "Свалка" for t in list_quest_contracts_for_character(bandit))
        modes = {m for m, *_ in list_available_travel_modes(bandit)}
        assert "bicycle" in modes and "foot" in modes

        assert build_alliance_overview(storage, 111)
        assert build_economy_overview(storage, 111)

        # Raids.
        storage.restore_energy(111, 100)
        storage.restore_energy(222, 100)
        raid_create = create_or_join_faction_raid(storage, 111, "Янтарь")
        assert raid_create.ok, raid_create.text
        raid_join = create_or_join_faction_raid(storage, 222, "Янтарь")
        assert raid_join.ok, raid_join.text
        raid_launch = launch_open_raid(storage, 111)
        assert raid_launch.ok, raid_launch.text
        assert raid_launch.tactical_raid
        from app.raid_grid import clear_raid_grid_session, get_raid_grid_session_by_player

        rgrid_session = get_raid_grid_session_by_player(storage, 111)
        assert rgrid_session is not None
        assert rgrid_session.player_ids == [111, 222]
        assert rgrid_session.participant_ids() == [111, 222]
        clear_raid_grid_session(storage, rgrid_session)
        storage.finish_raid(int(rgrid_session.raid_id), status="cancelled", result_text="smoke cleanup")
        assert build_raids_overview(storage, 111)

        # Depot raids (warehouse/garage) — tactical map + legacy raid_kind fix.
        from app.game_logic import (
            create_or_join_depot_raid,
            get_faction_garage,
            _set_faction_garage,
            resolve_open_raid_kind,
        )

        bandit_garage = get_faction_garage(storage, "Бандиты")
        bandit_garage["gasoline"] = 4
        _set_faction_garage(storage, "Бандиты", bandit_garage)
        storage.change_faction_warehouse_item("Бандиты", "ammo_pack", 6)
        depot_create = create_or_join_depot_raid(storage, 111, "Бандиты", depot="warehouse")
        assert depot_create.ok, depot_create.text
        depot_join = create_or_join_depot_raid(storage, 222, "Бандиты", depot="warehouse")
        assert depot_join.ok, depot_join.text
        assert depot_join.payload and depot_join.payload.get("notify")
        join_notify = depot_join.payload["notify"]
        assert any(int(item[0]) == 222 for item in join_notify)
        assert any(int(item[0]) == 111 for item in join_notify)
        open_depot = storage.get_open_raid_for_faction("Долг")
        assert open_depot is not None
        with storage._connect() as conn:
            conn.execute("UPDATE raids SET raid_kind = 'lair' WHERE id = ?", (int(open_depot["id"]),))
        open_depot = storage.get_open_raid_for_faction("Долг")
        assert resolve_open_raid_kind(open_depot) == "warehouse"
        depot_launch = launch_open_raid(storage, 111)
        assert depot_launch.ok, depot_launch.text
        assert depot_launch.tactical_raid
        depot_session = get_raid_grid_session_by_player(storage, 111)
        assert depot_session is not None
        assert depot_session.raid_kind == "warehouse"
        from app.raid_grid import LOOT_ZONE_LABELS, render_rgrid_frame

        assert LOOT_ZONE_LABELS["warehouse"] == "СКЛАД"
        render_rgrid_frame(storage, depot_session, 111)
        clear_raid_grid_session(storage, depot_session)
        storage.finish_raid(int(depot_session.raid_id), status="cancelled", result_text="smoke depot cleanup")

        # Mutants must not occupy player cells (melee from adjacent, lunge and return).
        from app.raid_grid import RaidGridSession, _hostile_turn

        mutant_session = RaidGridSession(
            session_id="mutest",
            raid_id=9999,
            raid_kind="lair",
            location_label="test",
            attacker_faction="Долг",
            player_ids=[111, 222],
        )
        mutant_session.grid = 9
        mutant_session.set_pos(111, (4, 4))
        mutant_session.set_pos(222, (6, 6))
        mutant_session.hp = {"111": 100, "222": 100}
        mutant_session.hostiles = [(4, 5)]
        mutant_session.hostile_types = ["mutant"]
        mutant_session.hostile_kinds = ["boar"]
        mutant_session.hostile_weapons = [""]
        _hostile_turn(storage, mutant_session)
        player_cells = {mutant_session.pos(111), mutant_session.pos(222)}
        assert all(hpos not in player_cells for hpos in mutant_session.hostiles)
        assert mutant_session.hostiles == [(4, 5)]

        # Defensive bots may reposition toward players (25% chance), never onto player cells.
        from unittest.mock import patch
        from app.tactical_combat import NPC_MOVE_CHANCE

        bot_session = RaidGridSession(
            session_id="botest",
            raid_id=9998,
            raid_kind="lair",
            location_label="test",
            attacker_faction="Долг",
            player_ids=[111],
        )
        bot_session.grid = 9
        bot_session.set_pos(111, (0, 4))
        bot_session.hp = {"111": 100}
        bot_session.hostiles = [(8, 4)]
        bot_session.hostile_types = ["bot"]
        bot_session.hostile_kinds = ["maloy"]
        bot_session.hostile_weapons = ["ПМ"]
        with patch("app.raid_grid.random.random", return_value=0.0):
            _hostile_turn(storage, bot_session)
        assert bot_session.hostiles != [(8, 4)]
        assert bot_session.hostiles[0] not in {(0, 4)}
        assert 0.0 < NPC_MOVE_CHANCE < 1.0

        # Melee from adjacent cell works even when hostile is on open ground.
        from app.raid_grid import rgrid_move, save_raid_grid_session

        melee_session = RaidGridSession(
            session_id="meleetest",
            raid_id=9994,
            raid_kind="lair",
            location_label="test",
            attacker_faction="Долг",
            player_ids=[111],
            turn_order=[111],
        )
        melee_session.grid = 9
        melee_session.set_pos(111, (4, 4))
        melee_session.hp = {"111": 100}
        melee_session.hostiles = [(5, 4)]
        melee_session.hostile_types = ["bot"]
        melee_session.hostile_weapons = ["ПМ"]
        save_raid_grid_session(storage, melee_session)
        move_result = rgrid_move(storage, 111, "right")
        assert move_result.ok, move_result.text
        after_melee = get_raid_grid_session_by_player(storage, 111)
        assert after_melee is not None
        assert after_melee.pos(111) == (4, 4)
        assert (5, 4) not in after_melee.hostiles
        clear_raid_grid_session(storage, after_melee)

        # Hostiles spawn on cover (mutants) and base_cover (bots), but may move freely later.
        from app.raid_grid import _build_lair_map, _spawn_hostiles

        spawn_session = RaidGridSession(
            session_id="spawntest",
            raid_id=9997,
            raid_kind="lair",
            location_label="test",
            attacker_faction="Долг",
            player_ids=[111, 222],
        )
        spawn_session.set_pos(111, (0, 0))
        spawn_session.set_pos(222, (1, 0))
        _build_lair_map(spawn_session)
        assert spawn_session.base_cover
        cover_set = set(spawn_session.cover)
        base_set = set(spawn_session.base_cover)
        _spawn_hostiles(spawn_session, bot_count=2, bot_tier=1)
        for i, pos in enumerate(spawn_session.hostiles):
            htype = spawn_session.hostile_types[i]
            if htype == "bot":
                assert pos in base_set, f"bot spawned off base_cover: {pos}"
            else:
                assert pos in cover_set, f"mutant spawned off cover: {pos}"

        # Stale rgrid session must not block a new raid launch.
        from app.raid_grid import clear_stale_raid_grid_session, start_raid_grid

        depot_create2 = create_or_join_depot_raid(storage, 111, "Бандиты", depot="garage")
        assert depot_create2.ok, depot_create2.text
        create_or_join_depot_raid(storage, 222, "Бандиты", depot="garage")
        open_garage = storage.get_open_raid_for_faction("Долг")
        assert open_garage is not None
        raid_id = int(open_garage["id"])
        stale_result, stale_session = start_raid_grid(
            storage,
            raid_id=raid_id,
            raid_kind="garage",
            location_label="Гараж «Бандиты»",
            attacker_faction="Долг",
            player_ids=[111, 222],
            target_faction="Бандиты",
        )
        assert stale_result.ok and stale_session is not None
        storage.start_raid_assault(raid_id)
        storage.finish_raid(raid_id, status="success", result_text="stale setup")
        clear_raid_grid_session(storage, stale_session)
        clear_stale_raid_grid_session(storage, 111)
        assert get_raid_grid_session_by_player(storage, 111) is None

        # Brown cover on all blue base_cover cells.
        from app.raid_grid import _apply_cover_on_base

        cover_session = RaidGridSession(
            session_id="covertest",
            raid_id=9996,
            raid_kind="lair",
            location_label="test",
            attacker_faction="Долг",
            player_ids=[111],
        )
        cover_session.base_cover = [(6, 0), (7, 1), (8, 2)]
        cover_session.cover = [(6, 0)]
        _apply_cover_on_base(cover_session)
        assert all(cell in set(cover_session.cover) for cell in cover_session.base_cover)

        # Revive down ally on adjacent cell with inventory medkit.
        from app.raid_grid import (
            _adjacent_down_allies,
            rgrid_revive_ally,
            save_raid_grid_session,
        )

        revive_session = RaidGridSession(
            session_id="revivetest",
            raid_id=9995,
            raid_kind="lair",
            location_label="test",
            attacker_faction="Долг",
            player_ids=[111, 222],
            turn_order=[111, 222],
        )
        revive_session.set_pos(111, (3, 3))
        revive_session.set_pos(222, (4, 3))
        revive_session.hp = {"111": 100, "222": 0}
        save_raid_grid_session(storage, revive_session)
        assert _adjacent_down_allies(revive_session, 111) == [222]
        storage.add_item(111, "medkit", 1)
        revive_result = rgrid_revive_ally(storage, 111, 222)
        assert revive_result.ok, revive_result.text
        revived = get_raid_grid_session_by_player(storage, 111)
        assert revived is not None
        assert revived.hp["222"] > 0
        clear_raid_grid_session(storage, revived)

        # NPC sprites pool.
        from app.npc_assets import NPC_SPRITE_KEYS, pick_npc_kind

        assert pick_npc_kind() in NPC_SPRITE_KEYS
        assert pick_npc_kind(marauder=True) in ("bandit", "maloy", "mercenary")

        # Arena on home base: training reward like easy quest after at least one wave.
        from app.arena_grid import arena_forfeit, get_arena_session, save_arena_session, start_arena
        from app.game_logic import QUESTS

        storage.set_location(111, faction_home_base("Долг"))
        storage.clear_travel(111)
        # Soft death: HP/inventory preserved, reward still paid if waves cleared.
        storage.change_health(111, 80 - storage.get_character(111, refresh_energy=False).health)
        storage.add_item(111, "medkit", 5)
        entry_hp = storage.get_character(111, refresh_energy=False).health
        entry_medkits = int(storage.get_character(111, refresh_energy=False).inventory.get("medkit", 0))
        money_before = storage.get_character(111, refresh_energy=False).money
        deaths_before = storage.get_player_stats(111)["deaths"]
        arena_start = start_arena(storage, 111)
        assert arena_start.ok, arena_start.text
        arena_session = get_arena_session(storage, 111)
        assert arena_session is not None
        assert arena_session.arena_medkits == 3
        assert arena_session.entry_hp == entry_hp
        assert arena_session.home_base == faction_home_base("Долг")
        arena_session.waves_cleared = 1
        arena_session.hp = 0  # simulate fall
        save_arena_session(storage, arena_session)
        from app.arena_grid import _end_session, _finalize_arena_reward

        arena_end = _end_session(
            storage,
            arena_session,
            _finalize_arena_reward(storage, arena_session, reason="пал"),
        )
        assert arena_end.ok, arena_end.text
        assert get_arena_session(storage, 111) is None
        after = storage.get_character(111, refresh_energy=False)
        assert after.health == entry_hp
        assert int(after.inventory.get("medkit", 0)) == entry_medkits
        assert storage.get_player_stats(111)["deaths"] == deaths_before
        money_after = after.money
        assert money_after - money_before == 100  # 100 RU за 1 пройденную волну

        # Forfeit path also soft-restores entry HP.
        money_before = storage.get_character(111, refresh_energy=False).money
        arena_start = start_arena(storage, 111)
        assert arena_start.ok, arena_start.text
        arena_session = get_arena_session(storage, 111)
        assert arena_session is not None
        arena_session.waves_cleared = 1
        save_arena_session(storage, arena_session)
        arena_end = arena_forfeit(storage, 111)
        assert arena_end.ok, arena_end.text
        assert get_arena_session(storage, 111) is None
        money_after = storage.get_character(111, refresh_energy=False).money
        assert money_after - money_before == 100  # 100 RU за 1 пройденную волну
        assert storage.get_character(111, refresh_energy=False).health == entry_hp

        # War lobby.
        war_create = create_or_join_war_lobby(storage, 111, "Свалка")
        assert war_create.ok, war_create.text
        war_join = create_or_join_war_lobby(storage, 222, "Свалка")
        assert war_join.ok, war_join.text
        too_few = launch_war_lobby(storage, 111)
        assert not too_few.ok, too_few.text
        assert "3" in too_few.text
        assert build_war_lobby_overview(storage, 111)
        assert "Создал:" in build_war_lobby_overview(storage, 111)
        from app.game_logic import dissolve_war_lobby, can_dissolve_war_lobby
        assert can_dissolve_war_lobby(storage, 111)
        dissolved = dissolve_war_lobby(storage, 111)
        assert dissolved.ok, dissolved.text
        assert "Открытых военных лобби нет" in build_war_lobby_overview(storage, 111)

        war_create2 = create_or_join_war_lobby(storage, 111, "Янтарь")
        assert war_create2.ok, war_create2.text
        storage.restore_energy(111, 100)
        storage.restore_energy(222, 100)
        create_or_join_war_lobby(storage, 222, "Янтарь")
        storage.create_character(501, "Duty3", "Мужской")
        storage.set_faction(501, "Долг")
        storage.restore_energy(501, 100)
        join_extra = create_or_join_war_lobby(storage, 501, "Янтарь")
        assert join_extra.ok, join_extra.text
        war_launch = launch_war_lobby(storage, 111)
        assert war_launch.text
        if war_launch.tactical_cwar:
            from app.clan_war_grid import (
                clear_cwar_session,
                cwar_status_caption,
                get_cwar_session_by_player,
                render_cwar_frame,
            )

            seen: set[str] = set()
            for pid in war_launch.notify_member_ids:
                sess = get_cwar_session_by_player(storage, pid)
                if sess and sess.session_id not in seen:
                    seen.add(sess.session_id)
                    assert len(render_cwar_frame(storage, sess, pid)) > 1000
                    assert "квадрат" not in cwar_status_caption(storage, sess, pid).lower()
                    clear_cwar_session(storage, sess)
            with storage._connect() as conn:
                conn.execute(
                    "UPDATE war_lobbies SET status = 'cancelled', finished_at = datetime('now') WHERE status = 'in_progress'"
                )
        neutral_for_lobby = next(
            loc["name"]
            for loc in storage.get_locations()
            if not loc.get("controlled_by") and str(loc.get("point_type") or "") != "база"
        )
        lobby_assault = attack_location(storage, 111, str(neutral_for_lobby))
        assert lobby_assault.ok, lobby_assault.text
        assert (lobby_assault.payload or {}).get("ncap_lobby")
        from app.neutral_capture import get_ncap_lobby_by_player, leave_ncap_lobby

        leave_result = leave_ncap_lobby(storage, 111)
        assert leave_result.ok, leave_result.text
        from app.player_busy import player_busy_reason

        assert get_ncap_lobby_by_player(storage, 111) is None
        assert player_busy_reason(storage, 111) is None

        # Smuggling: tactical grid mission with route checkpoints.
        from app.game_logic import (
            start_smuggling_run,
            resolve_smuggling_if_pending,
            get_active_smuggling,
            abandon_smuggling_run,
            build_smuggling_overview,
            begin_smuggling_travel_after_grid,
            SMUGGLING_REWARD_MIN,
            SMUGGLING_REWARD_MAX,
            roll_arrival_encounter,
        )
        from app.smuggle_mission import (
            get_smuggle_session,
            move_smuggle_mission,
            save_smuggle_session,
        )
        from app.quest_mission import GRID_SIZE, MAX_MOVES, LOCATION_DANGER

        overview = build_smuggling_overview(storage, 111)
        assert str(SMUGGLING_REWARD_MIN) in overview and str(SMUGGLING_REWARD_MAX) in overview
        assert "маршрут" in overview.lower() or "карте" in overview.lower()

        storage.set_location(111, "Росток")
        _ = roll_arrival_encounter(storage, 111, "Росток")

        storage.set_active_contract(111, None)
        storage.restore_energy(111, 100)
        storage.set_location(111, "Росток")
        if storage.get_character(111, refresh_energy=False).truck_owned:
            garage_deposit_truck(storage, 111)
        smuggle_start = start_smuggling_run(storage, 111, "Болото", transport_mode="foot")
        assert smuggle_start.ok, smuggle_start.text
        assert get_active_smuggling(storage, 111)
        assert smuggle_start.payload and smuggle_start.payload.get("mission_image")
        assert "контрабанд" in smuggle_start.text.lower()
        session = get_smuggle_session(storage, 111)
        assert session is not None
        assert len(session.route) == 3
        assert session.route[0] == (0, GRID_SIZE - 1)
        assert session.route[1] == (GRID_SIZE - 1, GRID_SIZE - 1)
        danger = LOCATION_DANGER.get("Росток", 2)
        expected_moves = max(10, int((MAX_MOVES + danger) * 2 // 3))
        assert session.max_moves == expected_moves

        busy_smuggle = player_busy_reason(storage, 111, skip="travel")
        assert busy_smuggle and "контрабанд" in busy_smuggle.lower()

        from app.player_busy import clear_all_activity_sessions, player_busy_reason as pbr

        clear_all_activity_sessions(storage, 111)
        assert get_smuggle_session(storage, 111) is None
        assert get_active_smuggling(storage, 111) is None
        assert pbr(storage, 111) is None

        smuggle_start = start_smuggling_run(storage, 111, "Болото", transport_mode="foot")
        assert smuggle_start.ok, smuggle_start.text

        move_result = move_smuggle_mission(storage, 111, "right")
        assert move_result.ok or move_result.payload
        assert resolve_smuggling_if_pending(storage, 111) is None

        session = get_smuggle_session(storage, 111)
        assert session is not None
        session.player = session.route[-1]
        session.route_index = len(session.route)
        save_smuggle_session(storage, 111, session)
        travel_start = begin_smuggling_travel_after_grid(storage, 111)
        assert travel_start.ok, travel_start.text
        assert travel_start.payload and travel_start.payload.get("mission_travel_started")
        assert get_smuggle_session(storage, 111) is None
        assert get_active_smuggling(storage, 111) is not None
        traveling = storage.get_character(111, refresh_energy=False)
        assert traveling is not None
        assert traveling.location == "Росток"
        assert traveling.travel_destination == "Болото"
        assert "прибытие" in travel_start.text.lower()

        from datetime import datetime, timedelta, timezone

        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with storage._connect() as conn:
            conn.execute(
                "UPDATE characters SET travel_arrives_at = ? WHERE telegram_id = ?",
                (past, 111),
            )
        storage.pop_due_travels()
        after_travel = storage.get_character(111, refresh_energy=False)
        assert after_travel is not None
        assert after_travel.location == "Болото"
        delivery = resolve_smuggling_if_pending(storage, 111)
        assert delivery
        assert get_active_smuggling(storage, 111) is None
        assert storage.pop_arrival_notice(111) == "Болото"
        assert abandon_smuggling_run(storage, 111).ok is False

        # Character career stats.
        from app.game_logic import build_character_stats_overview

        storage.add_player_stat(111, "quests_completed", 3)
        storage.add_player_stat(111, "artifacts_found", 2)
        storage.add_player_stat(111, "wars_won", 1)
        storage.add_player_stat(111, "money_earned", 500)
        storage.change_health(111, -10_000)
        stats = storage.get_player_stats(111)
        assert stats["quests_completed"] >= 3
        assert stats["artifacts_found"] >= 2
        assert stats["wars_won"] >= 1
        assert stats["money_earned"] >= 500
        assert stats["deaths"] >= 1
        assert stats["raids_completed"] >= 0
        stats_text = build_character_stats_overview(storage, 111)
        assert "Заданий выполнено" in stats_text
        assert "Ценных артефактов найдено" in stats_text
        assert "Смертей" in stats_text
        storage.change_health(111, 100)

        # Market + lots.
        from app.game_logic import (
            buy_first_market_lot,
            create_duel_challenge,
            accept_duel,
            propose_alliance,
            process_due_travels,
        )
        from app.html_utils import html_safe

        assert html_safe("<test>") == "&lt;test&gt;"
        assert html_safe(None) == ""

        # Travel arrival notice on next action.
        storage.set_location(111, "Росток")
        travel3 = travel_to(storage, 111, "НИИ Агропром")
        assert travel3.ok, travel3.text
        past3 = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with storage._connect() as conn:
            conn.execute(
                "UPDATE characters SET travel_arrives_at = ? WHERE telegram_id = ?",
                (past3, 111),
            )
        dest = storage.resolve_travel_if_due(111)
        assert dest == "НИИ Агропром"
        assert storage.pop_arrival_notice(111) == "НИИ Агропром"
        assert storage.pop_arrival_notice(111) is None

        # Periodic travel push for idle player.
        storage.set_location(222, "Росток")
        travel4 = travel_to(storage, 222, "Болото")
        assert travel4.ok, travel4.text
        past4 = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with storage._connect() as conn:
            conn.execute(
                "UPDATE characters SET travel_arrives_at = ? WHERE telegram_id = ?",
                (past4, 222),
            )
        due = process_due_travels(storage)
        assert (222, "Болото") in due
        assert storage.get_character(222, refresh_energy=False).location == "Болото"

        # Duels.
        storage.restore_energy(111, 100)
        storage.restore_energy(222, 100)
        duel, target_msg = create_duel_challenge(storage, 111, 222)
        assert duel.ok, duel.text
        assert target_msg and "дуэль" in target_msg.lower()
        duel_result, challenger_msg = accept_duel(storage, 222, 111)
        assert duel_result.ok, duel_result.text
        assert challenger_msg
        assert duel_result.payload and duel_result.payload.get("duel_started")
        from app.duel_grid import get_duel_session_by_player, duel_shoot, render_duel_frame

        session = get_duel_session_by_player(storage, 111)
        assert session is not None
        assert session.challenger_id == 111
        assert session.target_id == 222
        assert len(render_duel_frame(storage, session, 111)) > 1000
        from app.player_busy import player_busy_reason

        assert player_busy_reason(storage, 111) is not None
        blocked_trade = buy_item(storage, 111, "medkit")
        assert not blocked_trade.ok
        shoot = duel_shoot(storage, 111, "right")
        assert shoot.ok or shoot.payload, shoot.text
        from app.duel_grid import duel_forfeit

        forfeit = duel_forfeit(storage, 111)
        assert forfeit.ok, forfeit.text
        assert get_duel_session_by_player(storage, 111) is None

        # Alliance notify payload.
        alliance = propose_alliance(storage, 111, "Бандиты")
        assert alliance.ok, alliance.text
        assert alliance.payload and alliance.payload.get("notify")

        # ensure one equipment exists in inventory
        buy_weapon = buy_item(storage, 111, "weapon_pm")
        assert buy_weapon.ok, buy_weapon.text
        lot_result = create_market_lot(storage, 111, "weapon_pm", 1)
        assert lot_result.ok, lot_result.text
        storage.change_money(222, 50000)
        market_buy = buy_first_market_lot(storage, 222)
        assert market_buy.ok, market_buy.text
        assert market_buy.payload and market_buy.payload.get("notify")
        lots_text, lots = build_market_lots_overview(storage, 222, limit=10)
        assert lots_text
        assert isinstance(lots, list)

        # Personal stash + death inventory loot.
        from app.game_logic import (
            deposit_to_personal_stash,
            withdraw_from_personal_stash,
            respawn_character,
            RESPAWN_COST_RU,
        )

        storage.set_location(111, faction_home_base("Долг"))
        storage.change_money(111, 5000)
        # Reset medkit stack for predictable death loot math.
        cur = storage.get_character(111, refresh_energy=False)
        have = int(cur.inventory.get("medkit", 0))
        if have > 0:
            storage.remove_item(111, "medkit", have)
        storage.add_item(111, "medkit", 10)
        put = deposit_to_personal_stash(storage, 111, "medkit", 3)
        assert put.ok, put.text
        assert storage.get_personal_stash(111).get("medkit") == 3
        assert storage.get_character(111, refresh_energy=False).inventory.get("medkit") == 7
        storage.change_health(111, -10_000)
        money_before = storage.get_character(111, refresh_energy=False).money
        revive = respawn_character(storage, 111)
        assert revive.ok, revive.text
        assert "доставили" in revive.text.lower() or "спасен" in revive.text.lower()
        after = storage.get_character(111, refresh_energy=False)
        assert after.health > 0
        assert after.money == money_before - RESPAWN_COST_RU
        assert after.inventory.get("medkit", 0) == 1  # 7 → keep 20% = 1
        assert storage.get_personal_stash(111).get("medkit") == 3
        take = withdraw_from_personal_stash(storage, 111, "medkit", 1)
        assert take.ok, take.text

        # Respawn on credit when broke; debt auto-collected from earnings.
        from app.game_logic import get_respawn_debt

        broke = int(storage.get_character(111, refresh_energy=False).money)
        if broke > 0:
            storage.change_money(111, -broke, skip_debt_collect=True)
        storage.change_health(111, -10_000)
        debt_revive = respawn_character(storage, 111)
        assert debt_revive.ok, debt_revive.text
        assert "долг" in debt_revive.text.lower()
        assert get_respawn_debt(storage, 111) == RESPAWN_COST_RU
        assert storage.get_character(111, refresh_energy=False).money == 0
        storage.change_money(111, 700)
        assert get_respawn_debt(storage, 111) == 0
        assert storage.get_character(111, refresh_energy=False).money == 200

        # Survival caps = 100; hunger/thirst death respawn resets needs and HP to 50%.
        from app.game_logic import remember_death_cause, effective_max_health
        from app.storage import SURVIVAL_NEED_MAX

        storage.adjust_survival(111, radiation_delta=500, hunger_delta=500, thirst_delta=500)
        capped = storage.get_character(111, refresh_energy=False)
        assert capped is not None
        assert capped.radiation == SURVIVAL_NEED_MAX == 100
        assert capped.hunger == 100
        assert capped.thirst == 100
        storage.change_health(111, -10_000)
        remember_death_cause(storage, 111, "hunger")
        storage.change_money(111, RESPAWN_COST_RU + 50, skip_debt_collect=True)
        hunger_revive = respawn_character(storage, 111)
        assert hunger_revive.ok, hunger_revive.text
        revived = storage.get_character(111, refresh_energy=False)
        assert revived is not None
        assert revived.radiation == 0 and revived.hunger == 0 and revived.thirst == 0
        assert revived.health == max(1, int(effective_max_health(revived)) // 2)
        assert "сброшены" in hunger_revive.text.lower() or "радиация" in hunger_revive.text.lower()

        # Rating top achievements: top-10 / top-3 / top-1.
        from app.game_logic import (
            ACHIEVEMENT_BY_KEY,
            _progress_and_unlock_achievements,
            _player_rating_rank,
        )

        assert "rating_top_10" in ACHIEVEMENT_BY_KEY
        assert "rating_top_3" in ACHIEVEMENT_BY_KEY
        assert "rating_top_1" in ACHIEVEMENT_BY_KEY
        # Reset ratings so 111 is clearly #1.
        for tid, pts in ((111, 9000), (222, 100), (333, 50)):
            with storage._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO player_stats(telegram_id, rating_points)
                    VALUES (?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE SET rating_points = excluded.rating_points
                    """,
                    (tid, pts),
                )
        assert _player_rating_rank(storage, 111, limit=10) == 1
        top_text = _progress_and_unlock_achievements(storage, 111)
        unlocked = storage.get_player_achievement_keys(111)
        assert "rating_top_10" in unlocked
        assert "rating_top_3" in unlocked
        assert "rating_top_1" in unlocked
        assert "Первый среди сталкеров" in top_text or "rating_top_1" in unlocked

        # Keyboard callback sanity (basic non-empty check).
        callbacks = _all_callback_data()
        assert "contract:refresh" in callbacks
        assert "stash:menu" in callbacks
        assert "hunt:up" in callbacks
        assert "upgrade:armor" in callbacks
        assert "upgrade:artifact_slot" in callbacks
        assert "help_event:join" in callbacks
        assert "special_event:join" in callbacks
        assert "monolith_war:join" in callbacks
        assert "monolith_war:start" in callbacks
        assert "coop:shoot:up" in callbacks
        assert "equip:upgrade:install" in callbacks
        assert "equip:upgrade:remove" in callbacks

        # Top gear: Nosorog + Gauss + Енот (бармен этап 4).
        from app.vendors import set_vendor_tier, get_vendor_tier, unlocked_vendor_item_keys

        set_vendor_tier(storage, 111, "barkeep", 1)
        assert "weapon_gauss" not in unlocked_vendor_item_keys("barkeep", 1)
        assert "weapon_raccoon" not in unlocked_vendor_item_keys("barkeep", 3)
        locked_gauss = buy_item(storage, 111, "weapon_gauss")
        assert not locked_gauss.ok
        set_vendor_tier(storage, 111, "barkeep", 4)
        assert get_vendor_tier(storage, 111, "barkeep") == 4
        assert "weapon_gauss" in unlocked_vendor_item_keys("barkeep", 4)
        assert "weapon_raccoon" in unlocked_vendor_item_keys("barkeep", 4)
        storage.change_money(111, 3_000_000, skip_debt_collect=True)
        buy_n = buy_item(storage, 111, "armor_nosorog")
        assert buy_n.ok, buy_n.text
        buy_g = buy_item(storage, 111, "weapon_gauss")
        assert buy_g.ok, buy_g.text
        buy_raccoon = buy_item(storage, 111, "weapon_raccoon")
        assert buy_raccoon.ok, buy_raccoon.text
        assert SHOP_ITEMS["weapon_raccoon"]["name"] == "Енот"
        from app.game_logic import (
            ARMOR_BLOCK_CHANCE_BY_NAME,
            ARMOR_MITIGATION_BY_NAME,
            ARMOR_RATING_BY_NAME,
            WEAPON_RATING_BY_NAME,
            armor_block_chance,
            armor_flat_mitigation,
            shop_armor_button_title,
            shop_gear_button_title,
            shop_weapon_button_title,
        )

        pm_label = shop_weapon_button_title("weapon_pm")
        assert "сила 2" in pm_label and "д." in pm_label and "10000" in pm_label
        leather_label = shop_armor_button_title("armor_leather")
        assert "сила 2" in leather_label and "10000" in leather_label
        assert "−5" in leather_label and ("б3%" in leather_label or "блок 3%" in leather_label)
        # Цена сразу после имени — не в хвосте, который режет клиент.
        assert leather_label.index("10000") < leather_label.index("сила")
        bike_label = shop_gear_button_title("bicycle")
        assert "×" in bike_label and "нагр." in bike_label
        assert "арт." in shop_gear_button_title("detector_otklik")
        fora_label = shop_weapon_button_title("weapon_fort12")
        assert "сила 3" in fora_label and "15000" in fora_label
        sawed_label = shop_weapon_button_title("weapon_sawedoff")
        assert "сила 3" in sawed_label and "17000" in sawed_label
        assert int(SHOP_ITEMS["weapon_pm"]["buy_price"]) == 10000
        assert int(SHOP_ITEMS["weapon_fort12"]["buy_price"]) == 15000
        assert int(SHOP_ITEMS["weapon_sawedoff"]["buy_price"]) == 17000
        assert int(SHOP_ITEMS["bicycle"]["buy_price"]) == 10500
        assert int(SHOP_ITEMS["niva"]["buy_price"]) == 100000
        assert int(SHOP_ITEMS["truck"]["buy_price"]) == 500000
        assert int(SHOP_ITEMS["detector_otklik"]["buy_price"]) == 3000
        assert int(SHOP_ITEMS["detector_medved"]["buy_price"]) == 40000
        assert int(SHOP_ITEMS["medkit"]["buy_price"]) == 500
        assert int(SHOP_ITEMS["medkit_army"]["buy_price"]) == 1000
        assert int(SHOP_ITEMS["medkit_science"]["buy_price"]) == 1500
        assert int(SHOP_ITEMS["antirad"]["buy_price"]) == 900
        assert int(SHOP_ITEMS["antibiotic"]["buy_price"]) == 3500
        assert int(SHOP_ITEMS["energy_drink"]["buy_price"]) == 600
        assert int(SHOP_ITEMS["vodka"]["buy_price"]) == 300
        assert int(SHOP_ITEMS["ammo_pistol"]["buy_price"]) == 50
        assert int(SHOP_ITEMS["ammo_shotgun"]["buy_price"]) == 90
        assert int(SHOP_ITEMS["ammo_rifle"]["buy_price"]) == 120
        assert int(SHOP_ITEMS["ammo_gauss"]["buy_price"]) == 300
        storage.change_money(111, 20_000, skip_debt_collect=True)
        bulk_ammo = buy_item(storage, 111, "ammo_pistol", amount=10)
        assert bulk_ammo.ok, bulk_ammo.text
        assert "×10" in bulk_ammo.text
        assert int(storage.get_character(111, refresh_energy=False).inventory.get("ammo_pistol", 0)) >= 10
        from app.game_logic import apply_n2o_to_travel_minutes, has_n2o_travel_boost, use_nitrous_oxide
        from app.vendors import (
            get_vendor_tier,
            set_vendor_tier,
            shop_item_vendor,
            unlocked_vendor_item_keys,
        )

        assert int(SHOP_ITEMS["nitrous_oxide"]["buy_price"]) == 1250
        assert shop_item_vendor("nitrous_oxide") == "tech"
        assert "nitrous_oxide" not in unlocked_vendor_item_keys("barkeep", 4)
        set_vendor_tier(storage, 111, "tech", 2)
        assert "nitrous_oxide" in unlocked_vendor_item_keys("tech", get_vendor_tier(storage, 111, "tech"))
        storage.change_money(111, 50_000, skip_debt_collect=True)
        bulk_n2o = buy_item(storage, 111, "nitrous_oxide", amount=5)
        assert bulk_n2o.ok, bulk_n2o.text
        assert "×5" in bulk_n2o.text
        assert int(storage.get_character(111, refresh_energy=False).inventory.get("nitrous_oxide", 0)) >= 5
        storage.add_item(111, "nitrous_oxide", 1)
        n2o_use = use_nitrous_oxide(storage, 111)
        assert n2o_use.ok, n2o_use.text
        assert has_n2o_travel_boost(storage, 111)
        reduced, n2o_note = apply_n2o_to_travel_minutes(storage, 111, 20)
        assert reduced == 10
        assert "азота" in n2o_note.lower()
        assert not has_n2o_travel_boost(storage, 111)
        from datetime import datetime, timedelta, timezone

        from app.game_logic import (
            apply_n2o_to_active_travel,
            can_use_n2o_during_travel,
            has_n2o_trip_boost,
            is_traveling,
        )

        storage.clear_travel(111)
        arrives = datetime.now(timezone.utc) + timedelta(seconds=100)
        storage.start_travel(111, "Янтарь", arrives, "foot")
        storage.add_item(111, "nitrous_oxide", 1)
        assert can_use_n2o_during_travel(storage, 111)
        in_travel = apply_n2o_to_active_travel(storage, 111)
        assert in_travel.ok, in_travel.text
        assert has_n2o_trip_boost(storage, 111)
        traveler_n2o = storage.get_character(111, refresh_energy=False)
        assert traveler_n2o is not None and is_traveling(traveler_n2o)
        assert traveler_n2o.travel_arrives_at is not None
        remaining_sec = (
            traveler_n2o.travel_arrives_at.replace(tzinfo=timezone.utc)
            - datetime.now(timezone.utc)
        ).total_seconds()
        assert remaining_sec <= 55
        assert not can_use_n2o_during_travel(storage, 111)
        double_n2o = apply_n2o_to_active_travel(storage, 111)
        assert not double_n2o.ok
        from app.game_logic import process_due_travels

        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with storage._connect() as conn:
            conn.execute(
                "UPDATE characters SET travel_arrives_at = ? WHERE telegram_id = ?",
                (past, 111),
            )
        process_due_travels(storage)
        assert not has_n2o_trip_boost(storage, 111)
        storage.start_travel(111, "Кордон", datetime.now(timezone.utc) + timedelta(seconds=80), "foot")
        storage.add_item(111, "nitrous_oxide", 1)
        assert can_use_n2o_during_travel(storage, 111)
        trip2 = apply_n2o_to_active_travel(storage, 111)
        assert trip2.ok, trip2.text
        storage.clear_travel(111)
        from app.keyboards import travel_in_transit_keyboard

        n2o_rows = travel_in_transit_keyboard(show_n2o_button=True).inline_keyboard
        assert any(btn.callback_data == "use:nitrous_oxide" for row in n2o_rows for btn in row)
        plain_rows = travel_in_transit_keyboard(show_n2o_button=False).inline_keyboard
        assert not any(
            btn.callback_data == "use:nitrous_oxide" for row in plain_rows for btn in row
        )
        from app.faction_bots import FACTION_BOT_COUNT_UPGRADE_COST

        assert FACTION_BOT_COUNT_UPGRADE_COST == 100_000
        assert round_shop_price(886) == 900
        assert round_shop_price(332) == 300
        assert round_shop_price(53) == 100
        assert round_shop_price(24) == 20
        assert WEAPON_RATING_BY_NAME["ПМ"] == 2
        assert WEAPON_RATING_BY_NAME["Фора-12"] == 3
        assert WEAPON_RATING_BY_NAME["Обрез"] == 3
        assert WEAPON_RATING_BY_NAME["Енот"] == 9
        assert WEAPON_RATING_BY_NAME["Гаусс-пушка"] == 10
        assert ARMOR_RATING_BY_NAME["Носорог"] == 9
        from app.game_logic import BASE_FORTIFY_COST_RU, TRADER_WEAPON_TIER_UPGRADE_COST
        from app.vendors import VENDOR_REP_THRESHOLDS, reputation_progress_label, reputation_to_tier

        assert BASE_FORTIFY_COST_RU == 100_000
        assert TRADER_WEAPON_TIER_UPGRADE_COST[2] == 120_000
        assert TRADER_WEAPON_TIER_UPGRADE_COST[5] == 1_200_000
        assert VENDOR_REP_THRESHOLDS == (200, 1000, 5000, 20000)
        assert reputation_to_tier(0) == 1
        assert reputation_to_tier(199) == 1
        assert reputation_to_tier(200) == 2
        assert reputation_to_tier(1000) == 3
        assert reputation_to_tier(5000) == 4
        assert reputation_to_tier(20000) == 5
        assert reputation_progress_label(100) == "100/200"
        assert reputation_progress_label(250) == "250/1000"
        from app.tactical_combat import weapon_shoot_range

        assert ARMOR_BLOCK_CHANCE_BY_NAME["Кожаная куртка"] == 3
        assert ARMOR_BLOCK_CHANCE_BY_NAME["Носорог"] == 20
        assert ARMOR_MITIGATION_BY_NAME["Кожаная куртка"] == 5
        assert ARMOR_MITIGATION_BY_NAME["Носорог"] == 23
        assert weapon_shoot_range("АКС-74У") == 2
        assert weapon_shoot_range("СПАС-12") == 2
        assert weapon_shoot_range("Винтарь ВС") == 3
        assert weapon_shoot_range("Енот") == 4
        assert weapon_shoot_range("Гаусс-пушка") == 4
        storage.set_equipment_item(111, "armor", "Носорог")
        storage.update_equipment_fields(111, {"armor_upgrade_level": 0})
        ch = storage.get_character(111)
        assert ch is not None
        assert armor_block_chance(ch) == 20
        assert armor_flat_mitigation(ch) == 23
        import random as _rnd

        _orig = _rnd.randint
        _rnd.randint = lambda a, b: 1  # блок срабатывает
        try:
            assert apply_incoming_damage(15, ch, min_damage=1) == 0
        finally:
            _rnd.randint = _orig
        ch = storage.get_character(111)
        assert ch is not None
        _rnd.randint = lambda a, b: 100  # блок не срабатывает
        try:
            # 15 − 23 смягчение − 0 апгрейд = -8, но нижняя граница = min_damage + 10 = 11
            assert apply_incoming_damage(15, ch, min_damage=1) == 11
        finally:
            _rnd.randint = _orig
        assert "Тяжёлая артиллерия" in buy_g.text or "Тяжёлая артиллерия" in buy_n.text or (
            "nosorog_gauss" in storage.get_player_achievement_keys(111)
        )
        assert "armor_nosorog" in SHOP_ITEMS
        assert int(SHOP_ITEMS["armor_nosorog"]["buy_price"]) == 900000
        assert int(SHOP_ITEMS["weapon_gauss"]["buy_price"]) == 900000
        assert int(SHOP_ITEMS["armor_upgrade"]["buy_price"]) == 5000
        assert "rank:menu" in callbacks
        assert "war:section:scenario" in callbacks
        assert "war:section:lobby" in callbacks
        assert "war:section:assault" not in callbacks
        assert "trade:vendor:barkeep" in callbacks
        assert "trade:upgrade:barkeep" in callbacks

        # Garage vehicle rental: withdraw returns to faction garage after 30 minutes.
        import json
        import random
        from datetime import datetime, timedelta, timezone

        from app.game_logic import (
            GARAGE_VEHICLE_RENTALS_META,
            _set_faction_garage,
            _steal_faction_garage,
            garage_withdraw_niva,
            get_faction_garage,
            process_due_garage_vehicle_rentals,
        )

        dolg_garage = get_faction_garage(storage, "Долг")
        dolg_garage["niva"] = 1
        dolg_garage["niva_durs"] = [77]
        _set_faction_garage(storage, "Долг", dolg_garage)
        bandit_garage = get_faction_garage(storage, "Бандиты")
        bandit_garage["niva"] = 0
        bandit_garage["niva_durs"] = []
        _set_faction_garage(storage, "Бандиты", bandit_garage)

        old_randint = random.randint

        def _force_steal(a: int, b: int) -> int:
            if a == 1 and b == 100:
                return 1
            return old_randint(a, b)

        random.randint = _force_steal
        try:
            steal_lines = _steal_faction_garage(storage, "Долг", "Бандиты")
        finally:
            random.randint = old_randint
        assert any("угнан" in line.lower() for line in steal_lines)
        assert get_faction_garage(storage, "Долг")["niva"] == 0
        assert get_faction_garage(storage, "Бандиты")["niva"] == 1
        assert storage.get_meta(GARAGE_VEHICLE_RENTALS_META) in (None, "[]", "")

        dolg_garage["niva"] = 1
        dolg_garage["niva_durs"] = [55]
        _set_faction_garage(storage, "Долг", dolg_garage)
        withdraw = garage_withdraw_niva(storage, 111)
        assert withdraw.ok, withdraw.text
        assert storage.get_character(111, refresh_energy=False).niva_owned
        assert get_faction_garage(storage, "Долг")["niva"] == 0

        raw_rentals = storage.get_meta(GARAGE_VEHICLE_RENTALS_META)
        assert raw_rentals
        rentals = json.loads(raw_rentals)
        rentals[0]["return_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        storage.set_meta(GARAGE_VEHICLE_RENTALS_META, json.dumps(rentals))
        due_rentals = process_due_garage_vehicle_rentals(storage)
        assert len(due_rentals) == 1
        assert get_faction_garage(storage, "Долг")["niva"] == 1
        assert get_faction_garage(storage, "Долг")["niva_durs"] == [55]
        assert not storage.get_character(111, refresh_energy=False).niva_owned

        # Season rating leaderboard + exclusive rewards.
        from app.game_logic import (
            RATING_SEASON_META_KEY,
            SEASON_REWARD_ITEM_KEYS,
            build_season_rating_overview,
            get_rating_season,
            process_rating_season,
        )

        assert "weapon_season_champion" in SEASON_REWARD_ITEM_KEYS
        assert "armor_season_bronze" in SEASON_REWARD_ITEM_KEYS
        assert "weapon_season_champion" not in SHOP_ITEMS
        denied_season_buy = buy_item(storage, 111, "weapon_season_champion")
        assert not denied_season_buy.ok
        assert "торговца" in denied_season_buy.text.lower() or "эксклюзив" in denied_season_buy.text.lower()

        for tid, pts in ((111, 500), (222, 300), (333, 100)):
            with storage._connect() as conn:
                storage._ensure_player_stats_row(conn, tid)
                conn.execute(
                    "UPDATE player_stats SET season_rating = ? WHERE telegram_id = ?",
                    (pts, tid),
                )
        season_text, _pg, _pages = build_season_rating_overview(storage, 111, page=0)
        assert "Сезонный рейтинг" in season_text
        assert "Чемпион Зоны" in season_text
        assert "#1" in season_text or "1." in season_text
        from app.game_logic import build_season_schedule_text

        schedule_text = build_season_schedule_text(storage)
        assert "Закончится:" in schedule_text
        assert "МСК" in schedule_text
        assert "UTC" in schedule_text
        assert "Осталось:" in schedule_text

        season = get_rating_season(storage)
        past_season = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        storage.set_meta(
            RATING_SEASON_META_KEY,
            json.dumps({**season, "ends_at": past_season}, ensure_ascii=False),
        )
        end_message = process_rating_season(storage)
        assert end_message
        assert "Чемпион Зоны" in end_message
        assert "Награды казне группировок" in end_message or "казну" in end_message.lower()
        inv111 = storage.get_character(111, refresh_energy=False).inventory
        assert inv111.get("weapon_season_champion", 0) >= 1
        assert inv111.get("armor_season_champion", 0) >= 1
        inv222 = storage.get_character(222, refresh_energy=False).inventory
        assert inv222.get("weapon_season_silver", 0) >= 1
        inv333 = storage.get_character(333, refresh_energy=False).inventory
        assert inv333.get("armor_season_bronze", 0) >= 1
        from app.season_chat_titles import (
            SEASON_CHAT_TITLE_BY_PLACE,
            SEASON_CHAT_TITLE_PENDING_META,
            ZONE_COMMON_CHAT_ID,
            ZONE_FACTION_CHAT_IDS,
            build_season_chat_title_jobs,
        )

        assert SEASON_CHAT_TITLE_BY_PLACE[1] == "Чемпион Зоны"
        assert SEASON_CHAT_TITLE_BY_PLACE[2] == "Серебро сезона"
        assert SEASON_CHAT_TITLE_BY_PLACE[3] == "Бронза сезона"
        assert all(len(t) <= 16 for t in SEASON_CHAT_TITLE_BY_PLACE.values())
        assert ZONE_COMMON_CHAT_ID == -1003958853707
        assert ZONE_FACTION_CHAT_IDS["Нейтралы"] == -1004295857240
        pending_raw = storage.get_meta(SEASON_CHAT_TITLE_PENDING_META) or ""
        pending = json.loads(pending_raw) if pending_raw else {}
        jobs = pending.get("jobs") or []
        assert jobs, "season chat titles must be queued"
        assert any(
            int(j.get("user_id") or 0) == 111
            and j.get("title") == "Чемпион Зоны"
            and int(j.get("chat_id") or 0) == ZONE_COMMON_CHAT_ID
            for j in jobs
        )
        # Faction chat job if winner has a faction.
        sample_jobs = build_season_chat_title_jobs(
            storage, [{"telegram_id": 111, "season_rating": 100}]
        )
        assert any(int(j["chat_id"]) == ZONE_COMMON_CHAT_ID for j in sample_jobs)
        from app.season_chat_titles import (
            SEASON_CHAT_TITLE_HOLDERS_META,
            apply_pending_season_chat_titles,
        )
        from unittest.mock import patch
        import asyncio

        storage.set_meta(
            SEASON_CHAT_TITLE_PENDING_META,
            json.dumps(
                {
                    "jobs": [
                        {"chat_id": ZONE_COMMON_CHAT_ID, "user_id": 111, "title": "Чемпион Зоны", "place": 1},
                        {"chat_id": ZONE_COMMON_CHAT_ID, "user_id": 222, "title": "Серебро сезона", "place": 2},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        async def _fake_promote(_bot, _chat_id, user_id, _title):
            if int(user_id) == 222:
                raise RuntimeError("temporary telegram error")

        with patch("app.season_chat_titles._promote_title_only", _fake_promote):
            notes = asyncio.run(apply_pending_season_chat_titles(object(), storage))
        assert any("111" in note for note in notes)
        pending_after_raw = storage.get_meta(SEASON_CHAT_TITLE_PENDING_META) or ""
        pending_after = json.loads(pending_after_raw) if pending_after_raw else {}
        pending_after_jobs = pending_after.get("jobs") or []
        assert len(pending_after_jobs) == 1
        assert int(pending_after_jobs[0].get("user_id") or 0) == 222
        holders_after_raw = storage.get_meta(SEASON_CHAT_TITLE_HOLDERS_META) or ""
        holders_after = json.loads(holders_after_raw) if holders_after_raw else {}
        holders = holders_after.get("holders") or []
        assert any(int(hh.get("user_id") or 0) == 111 for hh in holders)

        # Atomic money: balance cannot go negative.
        bal_before = int(storage.get_character(111, refresh_energy=False).money)
        assert not storage.change_money(111, -(bal_before + 1))
        assert int(storage.get_character(111, refresh_energy=False).money) == bal_before

        # Daily login: second claim same day rejected.
        from app.game_logic import claim_daily_login

        daily1 = claim_daily_login(storage, 333)
        assert daily1.ok, daily1.text
        daily2 = claim_daily_login(storage, 333)
        assert not daily2.ok
        assert "уже получена" in daily2.text.lower()

        # Neutral capture: lobby min 2, one assault per location at a time.
        from app.neutral_capture import (
            NCAP_MIN_MEMBERS,
            create_or_join_ncap_lobby,
            get_ncap_lobby_by_player,
            get_ncap_session,
            join_ncap_lobby,
            ncap_forfeit,
            start_ncap_from_lobby,
        )

        neutral_loc = next(
            loc["name"]
            for loc in storage.get_locations()
            if not loc.get("controlled_by") and str(loc.get("point_type") or "") != "база"
        )
        storage.restore_energy(111, 100)
        storage.restore_energy(222, 100)
        lobby_create = create_or_join_ncap_lobby(storage, 111, str(neutral_loc))
        assert lobby_create.ok
        assert get_ncap_lobby_by_player(storage, 111) is not None
        too_few, _ = start_ncap_from_lobby(storage, 111)
        assert not too_few.ok
        assert str(NCAP_MIN_MEMBERS) in too_few.text
        join_ncap_lobby(storage, 222, get_ncap_lobby_by_player(storage, 111).lobby_id)
        ncap_start, _ = start_ncap_from_lobby(storage, 111)
        assert ncap_start.ok, ncap_start.text
        assert get_ncap_session(storage, 111) is not None
        ncap_busy = create_or_join_ncap_lobby(storage, 222, str(neutral_loc))
        assert not ncap_busy.ok
        assert "захват" in ncap_busy.text.lower() or "штурм" in ncap_busy.text.lower()
        ncap_forfeit(storage, 111)
        assert get_ncap_session(storage, 111) is None

        # Coop lobby creation.
        from app.coop_mission import create_coop_lobby, get_coop_lobby_by_player, save_coop_session

        storage.restore_energy(111, 100)
        coop = create_coop_lobby(storage, 111)
        assert coop.ok, coop.text
        assert get_coop_lobby_by_player(storage, 111) is not None

        # active_player() must not mutate active_index; downed defer death.
        from app.coop_mission import CoopMissionSession
        from app.tactical_roster import is_downed_in_group_session, resolve_active_player

        coop_sess = CoopMissionSession(
            session_id="t",
            lobby_id="l",
            location="Кордон",
            player_ids=[111, 222],
            turn_order=[111, 222],
            active_index=0,
            hp={"111": 0, "222": 50},
        )
        idx_before = coop_sess.active_index
        assert coop_sess.active_player() == 222
        assert coop_sess.active_player() == 222
        assert coop_sess.active_index == idx_before
        assert is_downed_in_group_session(coop_sess, 111)
        assert resolve_active_player(coop_sess, check_evacuated=True) == 222

        from app.bot import _tactical_downed_message

        save_coop_session(
            storage,
            CoopMissionSession(
                session_id="downed-dead",
                lobby_id="ld",
                location="Кордон",
                player_ids=[111, 222],
                turn_order=[111, 222],
                hp={"111": 0, "222": 50},
            ),
        )
        storage.change_health(111, -storage.get_character(111, refresh_energy=False).health)
        assert _tactical_downed_message(storage, 111) is None
        from app.coop_mission import clear_coop_session, get_coop_session_by_player

        dead_coop = get_coop_session_by_player(storage, 111)
        if dead_coop is not None:
            clear_coop_session(storage, dead_coop)
        storage.change_health(111, 100)

        from app.bot import resolve_dead_player

        storage.change_health(111, 100)
        player_alive = storage.get_character(111, refresh_energy=False)
        assert player_alive is not None and player_alive.health > 0
        dead = resolve_dead_player(storage, 111, refresh_survival=False)
        assert dead is None
        player_still = storage.get_character(111, refresh_energy=False)
        assert player_still is not None and player_still.health > 0

        from app.coop_mission import _finish_success as coop_finish_success
        from app.duel_grid import DuelGridSession
        from app.smuggle_mission import (
            SmuggleMissionSession,
            _fail_smuggle_run,
            save_smuggle_session,
        )
        from app.tactical_hp import sync_session_hp_to_db

        storage.change_health(111, 80)
        storage.change_health(222, 80)
        coop_win = CoopMissionSession(
            session_id="win-dead",
            lobby_id="lw",
            location="Кордон",
            player_ids=[111, 222],
            hp={"111": 0, "222": 40},
            objectives=[(2, 2)],
            collected=[(2, 2)],
            death_causes={"111": "mutant"},
        )
        win_result = coop_finish_success(storage, coop_win)
        assert win_result.ok
        assert 111 in (win_result.payload or {}).get("dead_players", [])
        dead_char = storage.get_character(111, refresh_energy=False)
        assert dead_char is not None and dead_char.health <= 0

        duel_sess = DuelGridSession(
            duel_id="d1",
            challenger_id=111,
            target_id=222,
            turn_order=[111, 222],
            hp={"111": 0, "222": 10},
        )
        assert not is_downed_in_group_session(duel_sess, 111)

        storage.change_health(111, 50)
        sync_session_hp_to_db(storage, 111, 0, force=False)
        assert storage.get_character(111, refresh_energy=False).health > 0
        sync_session_hp_to_db(storage, 111, 0, force=True)
        assert storage.get_character(111, refresh_energy=False).health <= 0

        storage.change_health(111, 0)
        save_smuggle_session(
            storage,
            111,
            SmuggleMissionSession(
                destination="Болото",
                origin="Росток",
                transport="foot",
                success_chance=50,
                player=(0, 5),
                route=[(0, 5), (5, 5), (2, 2)],
                moves=99,
                max_moves=10,
            ),
        )
        money_before = storage.get_character(111, refresh_energy=False).money
        timeout_text = _fail_smuggle_run(storage, 111, "timeout test")
        assert "timeout" in timeout_text
        assert storage.get_character(111, refresh_energy=False).money == money_before

        from app.game_logic import ActionResult
        from app.raid_grid import RaidGridSession, _end_session

        storage.change_health(111, 100)
        storage.change_health(222, 100)
        raid_sess = RaidGridSession(
            session_id="r1",
            raid_id=1,
            raid_kind="lair",
            location_label="Припять",
            attacker_faction="Долг",
            player_ids=[111, 222],
            hp={"111": 0, "222": 55},
            turn_order=[111, 222],
        )
        raid_result = ActionResult(
            True,
            "ok",
            payload={"rgrid_done": True, "success": True, "member_ids": [111, 222]},
        )
        _end_session(storage, raid_sess, raid_result)
        assert storage.get_character(111, refresh_energy=False).health <= 0
        assert storage.get_character(222, refresh_energy=False).health == 55

        storage.change_health(111, 100)
        coop_fail = CoopMissionSession(
            session_id="fail-w",
            lobby_id="lf",
            location="Кордон",
            player_ids=[111],
            hp={"111": 0},
        )
        from app.coop_mission import _finish_fail

        _finish_fail(storage, coop_fail, "test fail")
        wounded = storage.get_character(111, refresh_energy=False)
        assert wounded is not None and wounded.health == 1

        from app.tactical_roster import format_player_name
        from app.tactical_turn import save_turn_if_seq_ok
        from app.smuggle_mission import check_smuggle_session_timeout
        from datetime import datetime, timedelta, timezone

        assert format_player_name(storage, 0) == "—"
        assert format_player_name(storage, 111) != "—"

        from app.game_logic import append_death_log_once, clear_death_notice_sent, build_death_log_text

        append_death_log_once(storage, 111, "Первая смерть в журнале.")
        append_death_log_once(storage, 111, "Повтор не должен попасть.")
        log_text = build_death_log_text(storage, 111)
        assert log_text.count("Первая смерть") == 1
        assert "Повтор не должен" not in log_text
        clear_death_notice_sent(storage, 111)

        from app.tactical_turn import patch_session_message_ids
        from app.coop_mission import (
            CoopMissionSession,
            _session_key,
            clear_coop_session,
            get_coop_session_by_player,
            save_coop_session,
        )

        patch_sess = CoopMissionSession(
            session_id="patch-seq",
            lobby_id="pl",
            location="Кордон",
            player_ids=[111],
            hp={"111": 80},
            turn_seq=7,
            message_ids={"111": 100},
        )
        save_coop_session(storage, patch_sess)
        patch_session_message_ids(
            storage,
            meta_key=_session_key("patch-seq"),
            message_ids={"111": 200},
            from_dict=CoopMissionSession.from_dict,
            save_fn=save_coop_session,
        )
        reloaded = get_coop_session_by_player(storage, 111)
        assert reloaded is not None
        assert reloaded.turn_seq == 7
        assert reloaded.message_ids.get("111") == 200
        clear_coop_session(storage, reloaded)

        from app.game_logic import respawn_character, RESPAWN_HEALTH, is_traveling

        save_coop_session(
            storage,
            CoopMissionSession(
                session_id="respawn-coop",
                lobby_id="lr",
                location="Кордон",
                player_ids=[111],
                hp={"111": 0},
                turn_order=[111],
            ),
        )
        dead_ch = storage.get_character(111, refresh_energy=False)
        assert dead_ch is not None
        storage.change_health(111, -dead_ch.health - 1)
        result = respawn_character(storage, 111)
        assert result.ok, result.text
        after = storage.get_character(111, refresh_energy=False)
        assert after is not None and after.health == RESPAWN_HEALTH
        assert get_coop_session_by_player(storage, 111) is None

        storage.set_location(111, faction_home_base("Долг"))
        travel = travel_to(storage, 111, "Болото")
        assert travel.ok, travel.text
        assert is_traveling(storage.get_character(111, refresh_energy=False))
        storage.change_health(111, -10_000)
        travel_respawn = respawn_character(storage, 111)
        assert travel_respawn.ok, travel_respawn.text
        after_travel = storage.get_character(111, refresh_energy=False)
        assert after_travel is not None
        assert not is_traveling(after_travel)
        assert after_travel.travel_destination is None

        save_smuggle_session(
            storage,
            111,
            SmuggleMissionSession(
                destination="Болото",
                origin="Росток",
                transport="foot",
                success_chance=50,
                player=(0, 5),
                route=[(0, 5), (5, 5), (2, 2)],
                moves=0,
                max_moves=20,
                started_at=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
            ),
        )
        idle = check_smuggle_session_timeout(storage, 111)
        assert idle is not None and idle.ok is False
        assert get_smuggle_session(storage, 111) is None

        from app.game_logic import admin_delete_player_account

        storage.create_character(999888777, "TempDeleteMe", "Мужской")
        storage.create_character(999888778, "TempInvitee", "Мужской")
        assert storage.record_referral(999888777, 111)
        assert storage.record_referral(999888778, 999888777)
        assert storage.has_referral_claim(999888777)
        assert storage.has_referral_claim(999888778)
        assert storage.character_exists(999888777)
        storage.set_meta("quest_mission:999888777", '{"test": true}')
        deleted = admin_delete_player_account(storage, 999888777)
        assert deleted.ok, deleted.text
        assert not storage.character_exists(999888777)
        assert storage.get_meta("quest_mission:999888777") is None
        assert not storage.has_referral_claim(999888777)
        assert not storage.has_referral_claim(999888778)
        assert not storage.is_nickname_taken("TempDeleteMe")

        from app.html_utils import nickname_validation_error

        assert nickname_validation_error("<bad>") is not None
        assert nickname_validation_error("Stalker") is None
        assert nickname_validation_error("📋 Задания") is not None

        _, _, missing_callbacks = _callback_handler_coverage()
        assert not missing_callbacks, f"Missing callback handlers: {', '.join(missing_callbacks)}"

        from app.keyboards import group_bot_link_inline_keyboard

        gkb = group_bot_link_inline_keyboard("telega_stalker_bot")
        btn = gkb.inline_keyboard[0][0]
        assert btn.text == "🔗 Ссылка на бота"
        assert btn.url == "https://t.me/telega_stalker_bot?start=from_group"

        # Label map sanity (no missing key for new items).
        assert "detector_otklik" in ITEM_LABELS
        assert "sleeping_bag" in ITEM_LABELS

        from app.enemy_hud import (
            HUD_SLOTS,
            default_hp_for_kind,
            draw_enemy_hud,
            hud_slots_from_kinds,
            hud_slots_from_raid,
        )
        from PIL import Image

        assert default_hp_for_kind("blind_dog") == 16
        slots = hud_slots_from_kinds(["blind_dog", "flesh"], ["bandit"])
        assert len(slots) == 3
        assert slots[0].kind == "blind_dog" and not slots[0].is_npc
        assert slots[2].is_npc
        raid_slots = hud_slots_from_raid(["bot", "mutant"], ["maloy", "blind_dog"])
        assert raid_slots[0].is_npc and not raid_slots[1].is_npc
        hud_canvas = Image.new("RGBA", (400, 300), (20, 20, 22, 255))
        draw_enemy_hud(
            hud_canvas,
            slots,
            panel_left=10,
            panel_top=10,
            panel_right=390,
            panel_bottom=290,
        )
        assert hud_canvas.size == (400, 300)

        from app.quest_mission import QuestMissionSession, render_mission_frame

        hud_session = QuestMissionSession(
            contract_key="easy_boloto",
            title="Зачистка",
            location="Кордон",
            kind="clear_mutant",
            difficulty="heavy",
            player=(0, 5),
            start=(0, 5),
            objectives=[],
            hazards=[(2, 2)],
            enemies=[(3, 3), (4, 4)],
            enemy_kinds=["blind_dog", "blind_dog"],
            npcs=[],
            npc_kinds=[],
        )
        hud_png = render_mission_frame(hud_session, storage.get_character(111, refresh_energy=False))
        assert len(hud_png) > 2000
        assert HUD_SLOTS == 6

        from app.game_logic import (
            FACTION_LORE,
            TOPUP_RATE_RU_PER_STAR,
            TRADER_SELL_CATALOG,
            TUTORIAL_COMPLETE_REWARD_RU,
            TUTORIAL_MENU_BUTTON,
            TUTORIAL_PAGES,
            format_faction_lore,
        )

        assert TOPUP_RATE_RU_PER_STAR == 150
        assert TRADER_SELL_CATALOG["weapons"][0] == "ammo_pistol"
        assert "ammo_pistol" not in TRADER_SELL_CATALOG["consumables"]
        assert set(FACTION_LORE) == {"Долг", "Свобода", "Нейтралы", "Бандиты", "Монолит"}
        for faction_name, lore in FACTION_LORE.items():
            assert "Как сформировал" in lore
            assert "Цель в Зоне" in lore
            assert format_faction_lore(faction_name) == lore
        assert TUTORIAL_MENU_BUTTON == "🔥 Как не сдохнуть"
        assert TUTORIAL_COMPLETE_REWARD_RU == 250
        assert len(TUTORIAL_PAGES) == 7
        assert any("арена" in body.lower() for _, body in TUTORIAL_PAGES)

        from app.keyboards import (
            pda_keyboard,
            topup_root_keyboard,
            trader_buy_consumables_keyboard,
            trader_buy_weapons_keyboard,
        )

        weapon_texts = [btn.text for row in trader_buy_weapons_keyboard().inline_keyboard for btn in row]
        assert any("Патрон" in text for text in weapon_texts)
        food_texts = [btn.text for row in trader_buy_consumables_keyboard().inline_keyboard for btn in row]
        assert not any("Патрон" in text for text in food_texts)
        pda_texts = [btn.text for row in pda_keyboard().keyboard for btn in row]
        assert TUTORIAL_MENU_BUTTON in pda_texts
        root_data = [btn.callback_data for row in topup_root_keyboard(has_faction=True).inline_keyboard for btn in row]
        assert "topup:menu:self" in root_data
        assert "topup:menu:faction" in root_data
        no_fac_data = [
            btn.callback_data for row in topup_root_keyboard(has_faction=False).inline_keyboard for btn in row
        ]
        assert "topup:menu:faction" not in no_fac_data

        from app.chat_medals import (
            forget_group_chat,
            get_chat_medal,
            medal_chat_targets,
            remember_group_chat,
            resolve_group_title,
            save_chat_medal,
            sanitize_medal_title,
        )
        from app.player_medals import MEDAL_CHAT_TITLES, chat_title_for_player
        from app.season_chat_titles import (
            ZONE_COMMON_CHAT_ID,
            ZONE_FACTION_CHAT_IDS,
            _is_custom_title_forbidden,
            _is_title_race_error,
            _member_status,
            _pick_dummy_rights,
            _title_only_promote_kwargs,
            zone_chat_label,
        )

        class _MemberStub:
            status = "member"

        class _AdminStub:
            class _Status:
                value = "administrator"

            status = _Status()

        assert _member_status(_MemberStub()) == "member"
        assert _member_status(_AdminStub()) == "administrator"
        assert zone_chat_label(ZONE_COMMON_CHAT_ID) == "общий чат Зоны"
        assert zone_chat_label(ZONE_FACTION_CHAT_IDS["Нейтралы"]) == "чат Нейтралы"

        class _BotAdminInviteOnly:
            status = "administrator"
            can_manage_video_chats = False
            can_pin_messages = False
            can_invite_users = True
            can_change_info = False

        class _BotAdminNoDummy:
            status = "administrator"
            can_manage_video_chats = False
            can_pin_messages = False
            can_invite_users = False
            can_change_info = False

        class _BotCreator:
            status = "creator"

        assert _pick_dummy_rights(_BotAdminInviteOnly()) == ["can_invite_users"]
        assert _pick_dummy_rights(_BotAdminNoDummy()) == []
        assert _pick_dummy_rights(_BotCreator())[0] == "can_manage_video_chats"
        kwargs = _title_only_promote_kwargs("can_manage_video_chats")
        assert kwargs["can_manage_video_chats"] is True
        assert kwargs["can_invite_users"] is False
        assert _is_title_race_error(RuntimeError("not enough rights to change custom title of the user"))
        assert _is_custom_title_forbidden(RuntimeError("Bad Request: not enough rights to change custom title of the user"))
        assert not _is_custom_title_forbidden(RuntimeError("RIGHT_FORBIDDEN"))
        from app.season_chat_titles import _is_promote_forbidden

        assert _is_promote_forbidden(RuntimeError("RIGHT_FORBIDDEN"))
        assert not _is_promote_forbidden(RuntimeError("not enough rights to change custom title of the user"))

        assert sanitize_medal_title("🔥Ветеран🔥") == "Ветеран"
        assert len(sanitize_medal_title("A" * 40)) == 16
        for title in MEDAL_CHAT_TITLES.values():
            assert 0 < len(title) <= 16
            assert sanitize_medal_title(title) == title
        assert ZONE_COMMON_CHAT_ID in medal_chat_targets(storage)
        for chat_id in ZONE_FACTION_CHAT_IDS.values():
            assert chat_id in medal_chat_targets(storage)
        remember_group_chat(storage, -100111222333)
        assert -100111222333 in medal_chat_targets(storage)
        forget_group_chat(storage, -100111222333)
        assert -100111222333 not in medal_chat_targets(storage)
        assert save_chat_medal(storage, 111, "Ветеран") == "Ветеран"
        assert get_chat_medal(storage, 111) == "Ветеран"
        assert resolve_group_title(storage, 111) == "Ветеран"

        from app.vendors import vendor_person_name, vendor_quest_label, VENDOR_REP_BY_DIFFICULTY
        from app.game_logic import QUEST_CONTRACTS, list_vendor_contracts_for_character
        from app.keyboards import trader_keyboard, ratings_keyboard

        assert vendor_person_name("Бандиты", "barkeep") == "Боров"
        assert vendor_person_name("Свобода", "tech") == "Дядька Яр"
        assert vendor_person_name("Долг", "medic") == "Митяй"
        assert vendor_person_name("Нейтралы", "barkeep") == "Суслов"
        assert "бандитов" in vendor_quest_label("Бандиты", "barkeep")
        duty_player = storage.get_character(111, refresh_energy=False)
        vendor_quests = list_vendor_contracts_for_character(duty_player, "barkeep")
        assert len(vendor_quests) == 4
        assert {item.difficulty for item in vendor_quests} == {"easy", "hard", "heavy", "impossible"}
        assert VENDOR_REP_BY_DIFFICULTY["easy"] == 2
        trader_names = [btn.text for row in trader_keyboard("Бандиты").inline_keyboard for btn in row]
        assert any("Боров" in text for text in trader_names)
        medal_data = [btn.callback_data for row in ratings_keyboard().inline_keyboard for btn in row]
        medal_labels = [btn.text for row in ratings_keyboard().inline_keyboard for btn in row]
        assert "ratings:medals" not in medal_data
        assert "ratings:achievements" in medal_data
        assert "ratings:factions" in medal_data
        assert any("Достижения и медали" in text for text in medal_labels)
        assert any("группировок" in text for text in medal_labels)

        from app.game_logic import compute_faction_dynamic_ratings, FACTION_RATING_PER_LOCATION

        faction_rows = compute_faction_dynamic_ratings(storage)
        assert len(faction_rows) == 5
        assert all("total" in row for row in faction_rows)
        dolg = next(row for row in faction_rows if row["faction"] == "Долг")
        assert int(dolg["total"]) == int(dolg["locations"]) * FACTION_RATING_PER_LOCATION + int(dolg["power_sum"])

        from app.keyboards import vendor_upgrade_keyboard

        upgrade_data = [
            btn.callback_data
            for row in vendor_upgrade_keyboard("barkeep").inline_keyboard
            for btn in row
        ]
        assert all(cb and "confirm" not in cb for cb in upgrade_data)

        bandit_player = storage.get_character(333, refresh_energy=False)
        barkeep_easy = [
            item.key
            for item in list_vendor_contracts_for_character(bandit_player, "barkeep")
            if item.difficulty == "easy"
        ]
        tech_easy = [
            item.key
            for item in list_vendor_contracts_for_character(bandit_player, "tech")
            if item.difficulty == "easy"
        ]
        assert barkeep_easy and tech_easy
        assert set(barkeep_easy).isdisjoint(set(tech_easy))
        assert QUEST_CONTRACTS["easy_escort_boloto"].work_location == "Болото"

        from app.bot import parse_target_and_int, resolve_player_id, _build_info_text, GROUP_CHAT_ALLOWED_COMMANDS

        assert "/badge" in GROUP_CHAT_ALLOWED_COMMANDS
        assert "/top" in GROUP_CHAT_ALLOWED_COMMANDS
        assert parse_target_and_int("LeaderDuty 250") == ("LeaderDuty", 250)
        assert resolve_player_id(storage, "111") == 111
        assert resolve_player_id(storage, "LeaderDuty") == 111
        assert resolve_player_id(storage, "@LeaderDuty") == 111
        assert resolve_player_id(storage, "999001") == 999001
        info_text = _build_info_text(duty_player)
        assert "/fixme" in info_text
        assert "/respawn" in info_text
        assert len(info_text) <= 4096

        from app.player_medals import (
            BADGE_TOP_KINDS,
            add_admin_medal_progress,
            format_medals_overview,
            format_rotating_tops,
            get_player_medal_keys,
            grant_medal,
            medals_nick_suffix,
            refresh_exclusive_and_rotating_medals,
            stars_to_rub,
            sync_player_medals,
        )

        assert stars_to_rub(167) >= 500
        add_admin_medal_progress(storage, 111, "mentor")
        assert "mentor" in get_player_medal_keys(storage, 111)
        assert chat_title_for_player(storage, 111) == "Наставник"
        assert resolve_group_title(storage, 111) == "Ветеран"
        from app.game_logic import format_inventory
        from app.profile_card import _player_name_with_medals, build_character_card

        assert medals_nick_suffix(storage, 111).startswith(" ")
        assert "👥" in medals_nick_suffix(storage, 111)
        inv_text = format_inventory(storage.get_character(111, refresh_energy=False), storage=storage)
        assert "👤 LeaderDuty" in inv_text
        nick_line = next(line for line in inv_text.splitlines() if line.startswith("👤 "))
        assert nick_line.index("LeaderDuty") < nick_line.index("👥")
        card = build_character_card(storage.get_character(111, refresh_energy=False), storage=storage)
        assert isinstance(card, (bytes, bytearray)) and len(card) > 100
        from PIL import Image, ImageDraw, ImageFont

        probe = Image.new("RGB", (400, 40))
        probe_draw = ImageDraw.Draw(probe)
        named = _player_name_with_medals(
            probe_draw,
            "LeaderDuty",
            medals_nick_suffix(storage, 111),
            ImageFont.load_default(),
            350,
        )
        assert named.startswith("Игрок: LeaderDuty")
        assert "👥" in named
        from app.image_text import contains_emoji, iter_text_runs, render_emoji_glyph

        emoji_font = Path(__file__).resolve().parent / "assets" / "fonts" / "NotoColorEmoji.ttf"
        assert emoji_font.is_file(), "missing assets/fonts/NotoColorEmoji.ttf"
        # Полный Noto Color Emoji ~10MB; урезанный файл даёт квадраты вместо эмодзи на картинках.
        assert emoji_font.stat().st_size > 5_000_000, (
            f"NotoColorEmoji.ttf looks truncated ({emoji_font.stat().st_size} bytes)"
        )

        assert contains_emoji("🛠 👥")
        assert contains_emoji("🔥 Схрон очень близко!")
        assert contains_emoji("❄ Холодно")
        assert not contains_emoji("Игрок: mercury")
        assert any(is_emoji and "👥" in chunk for is_emoji, chunk in iter_text_runs("Игрок: mercury 👥"))
        wrench = render_emoji_glyph("🛠", 24)
        people = render_emoji_glyph("👥", 24)
        fire = render_emoji_glyph("🔥", 24)
        snow = render_emoji_glyph("❄", 24)
        assert wrench is not None and people is not None
        assert fire is not None and snow is not None
        for glyph in (wrench, people, fire, snow):
            pix = glyph.load()
            colors = {
                pix[x, y][:3]
                for x in range(glyph.size[0])
                for y in range(glyph.size[1])
                if pix[x, y][3] > 40
            }
            assert len(colors) >= 8
        mixed = Image.new("RGB", (420, 48), (21, 21, 26))
        mixed_draw = ImageDraw.Draw(mixed)
        mixed_font = ImageFont.truetype(
            str(Path(__file__).resolve().parent / "assets" / "fonts" / "NotoSans-Regular.ttf"),
            22,
        )
        mixed_draw.text((8, 10), "Игрок: mercury 🛠 👥", fill=(240, 240, 240), font=mixed_font)
        mixed_pix = mixed.load()
        mixed_colors = {
            mixed_pix[x, y]
            for x in range(mixed.size[0])
            for y in range(mixed.size[1])
            if mixed_pix[x, y] not in {(21, 21, 26), (240, 240, 240)}
        }
        assert len(mixed_colors) >= 12
        add_admin_medal_progress(storage, 111, "finder", 3)
        assert "finder" in get_player_medal_keys(storage, 111)
        add_admin_medal_progress(storage, 111, "idea", 5)
        assert "idea" in get_player_medal_keys(storage, 111)
        storage.add_player_stat(111, "rating_points", 700)
        sync_player_medals(storage, 111)
        assert "beta" in get_player_medal_keys(storage, 111)
        grant_medal(storage, 111, "top_all")
        refresh_exclusive_and_rotating_medals(storage, force_rotating=True)
        assert "richest" in get_player_medal_keys(storage, 111) or storage.top_player_by_money() != 111
        import inspect

        assert "top" in BADGE_TOP_KINDS
        assert "топ" in BADGE_TOP_KINDS
        assert "MAX(COALESCE(c.nickname" in inspect.getsource(storage.top_stars_donors)
        money_top = storage.top_players_by_money(limit=5)
        assert money_top and int(money_top[0]["telegram_id"]) == storage.top_player_by_money()
        arts_top = storage.top_players_by_stat("artifacts_found", limit=5)
        assert arts_top and int(arts_top[0]["value"]) >= 2
        tops_text = format_rotating_tops(storage)
        assert "Деньги на руках" in tops_text
        assert "Ценных артефактов найдено" in tops_text
        assert "LeaderDuty" in tops_text
        assert "id 111" in tops_text
        assert "&lt;" not in tops_text
        from app.bot import _send_badge_tops

        assert "parse_mode=None" in inspect.getsource(_send_badge_tops)
        from app.game_logic import build_achievements_overview

        achievements_text = build_achievements_overview(storage, 111)
        assert "🏅 Медали:" in achievements_text
        assert "Бета-тестировщик" in achievements_text
        assert "✅ 👥 Наставник" in achievements_text
        assert len(achievements_text) <= 4096
        medals_text = format_medals_overview(storage, 111)
        assert "🔒 🛠 Без тебя этого бы не было" in medals_text
        assert "✅ ⚙ Бета-тестировщик" in medals_text

        before_money = int(storage.get_character(111, refresh_energy=False).money)
        treasury_before = {
            str(row["name"]): int(row["treasury"]) for row in storage.get_factions()
        }
        ok, dup = storage.apply_topup_payment(111, "pay-self-1", 1, 150)
        assert ok and not dup
        assert int(storage.get_character(111, refresh_energy=False).money) == before_money + 150
        ok, dup = storage.apply_topup_payment(111, "pay-self-1", 1, 150)
        assert (not ok) and dup
        ok, dup = storage.apply_topup_payment(111, "pay-gp-1", 5, 750, to_faction="Долг")
        assert ok and not dup
        assert int(storage.get_character(111, refresh_energy=False).money) == before_money + 150
        treasury_after = {
            str(row["name"]): int(row["treasury"]) for row in storage.get_factions()
        }
        assert treasury_after["Долг"] == treasury_before["Долг"] + 750
        donors = storage.top_stars_donors(limit=5)
        assert donors and int(donors[0]["telegram_id"]) == 111
        assert int(donors[0]["value"]) >= 6

        # Live ETA «В пути» — один message_id, без дублей при гонке tick + старт поездки.
        import asyncio
        from datetime import datetime, timedelta, timezone
        from unittest.mock import AsyncMock, MagicMock, patch

        from app import bot as bot_mod
        from app.game_logic import travel_status_text

        traveler = storage.get_character(111, refresh_energy=False)
        assert traveler is not None
        if traveler.travel_destination:
            # Дождаться/сбросить активный переход, если остался от прошлых шагов.
            past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            with storage._connect() as conn:
                conn.execute(
                    "UPDATE characters SET travel_arrives_at = ? WHERE telegram_id = ?",
                    (past, 111),
                )
            storage.resolve_travel_if_due(111)
        travel_ok = travel_to(storage, 111, "Рыжий лес", transport_mode="foot")
        if not travel_ok.ok:
            travel_ok = travel_to(storage, 111, "Кордон", transport_mode="foot")
        assert travel_ok.ok, travel_ok.text
        traveler = storage.get_character(111, refresh_energy=False)
        assert traveler is not None and traveler.travel_destination
        status = travel_status_text(traveler)
        assert status is not None and "Осталось ехать" in status

        bot_mod._travel_eta_locks.clear()
        storage.delete_meta("travel_eta_msg:111")
        send_count = {"n": 0}

        class _FakeSent:
            def __init__(self, mid: int):
                self.message_id = mid

        async def _fake_send(chat_id, text, **_kwargs):
            send_count["n"] += 1
            await asyncio.sleep(0.015)
            return _FakeSent(9000 + send_count["n"])

        async def _fake_edit(*, chat_id, message_id, text, **_kwargs):
            await asyncio.sleep(0.005)
            return True

        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock(side_effect=_fake_send)
        fake_bot.edit_message_text = AsyncMock(side_effect=_fake_edit)

        async def _race():
            # Имитация: старт поездки + секундный tick одновременно.
            await asyncio.gather(
                bot_mod.publish_travel_live_eta(fake_bot, 111),
                bot_mod.upsert_travel_eta_message(fake_bot, 111, status),
                bot_mod.upsert_travel_eta_message(fake_bot, 111, status + "\n."),
                bot_mod.publish_travel_live_eta(fake_bot, 111),
            )

        with patch.object(bot_mod, "get_storage", return_value=storage):
            asyncio.run(_race())

        assert send_count["n"] == 1, f"expected one ETA send, got {send_count['n']}"
        mid = storage.get_meta("travel_eta_msg:111")
        assert mid is not None
        # clear meta не должен выкидывать лок из словаря (иначе снова дубли).
        lock_before = bot_mod._travel_eta_lock(111)
        bot_mod.clear_travel_eta_message_id(storage, 111)
        assert bot_mod._travel_eta_locks.get(111) is lock_before
        assert storage.get_meta("travel_eta_msg:111") is None
        await_finish = asyncio.run(bot_mod.finish_travel_eta_message(storage, 111))
        assert await_finish is None
        assert 111 not in bot_mod._travel_eta_locks

        # Next-features pack: status, bazaar tax/confirm, trade, faction power cap.
        from app.game_logic import (
            BAZAAR_FACTION_TAX_PERCENT,
            EXCHANGE_SELL_FEE_PERCENT,
            FACTION_RATING_POWER_CAP_PER_MEMBER,
            FACTION_SEASON_TREASURY_REWARDS,
            build_faction_rating_overview,
            build_quick_status_text,
            compute_faction_dynamic_ratings,
            create_custom_exchange_lot,
            preview_bazaar_lot,
            buy_exchange_lot,
        )
        from app.player_trade import (
            accept_trade_offer,
            create_trade_offer,
            decline_trade_offer,
            get_trade_offer,
        )
        from app.keyboards import (
            bazaar_confirm_buy_kb,
            main_menu_keyboard,
            pda_keyboard,
            trade_offer_keyboard,
        )
        from app.fsm_nav import REPLY_NAV_BUTTONS

        status_text = build_quick_status_text(storage, 111)
        assert "Статус" in status_text
        assert "LeaderDuty" in status_text
        menu_labels = {btn.text for row in main_menu_keyboard().keyboard for btn in row}
        assert "📡 Статус" in menu_labels
        pda_labels = {btn.text for row in pda_keyboard(is_leader=True).keyboard for btn in row}
        assert "🤝 Обмен" in pda_labels
        assert "📡 Статус" in REPLY_NAV_BUTTONS
        assert "🤝 Обмен" in REPLY_NAV_BUTTONS

        eco_text = build_economy_overview(storage, 111)
        assert f"{BAZAAR_FACTION_TAX_PERCENT}%" in eco_text
        assert FACTION_SEASON_TREASURY_REWARDS[1] == 25000

        ratings = compute_faction_dynamic_ratings(storage)
        assert ratings
        for row in ratings:
            assert int(row["power_sum"]) <= int(row["power_cap"])
            assert int(row["power_cap"]) == FACTION_RATING_POWER_CAP_PER_MEMBER * max(
                1, int(row["members"])
            )
        faction_overview = build_faction_rating_overview(storage, 111)
        assert "потолок" in faction_overview.lower()

        def _treasury(faction: str) -> int:
            with storage._connect() as conn:
                row = conn.execute(
                    "SELECT treasury FROM factions WHERE name = ?", (faction,)
                ).fetchone()
            return int(row["treasury"] or 0) if row else 0

        storage.add_item(111, "medkit", 3)
        storage.change_money(222, 50_000)
        before_treasury = _treasury("Долг")
        lot_price = 1000
        lot_res = create_custom_exchange_lot(storage, 111, "medkit", 1, price=lot_price)
        assert lot_res.ok, lot_res.text
        open_lots = [
            a
            for a in storage.list_open_auctions()
            if int(a["seller_id"]) == 111 and str(a["item_key"]) == "medkit"
        ]
        assert open_lots
        lot_id = int(open_lots[-1]["id"])
        preview = preview_bazaar_lot(storage, lot_id, market=False)
        assert preview.ok, preview.text
        assert "Подтверди" in preview.text
        confirm_kb = bazaar_confirm_buy_kb("auction", lot_id, "eco:auction:list")
        assert any(
            (btn.callback_data or "").startswith("eco:auction:confirm:")
            for row in confirm_kb.inline_keyboard
            for btn in row
        )
        buy_res = buy_exchange_lot(storage, 222, lot_id)
        assert buy_res.ok, buy_res.text
        fee = max(1, int(round(lot_price * (EXCHANGE_SELL_FEE_PERCENT / 100))))
        expected_tax = max(0, min(fee, int(round(lot_price * (BAZAAR_FACTION_TAX_PERCENT / 100)))))
        after_treasury = _treasury("Долг")
        assert after_treasury == before_treasury + expected_tax

        # Same location for trade.
        storage.clear_travel(111)
        storage.clear_travel(222)
        from app.player_busy import force_clear_live_player_sessions, player_busy_reason

        force_clear_live_player_sessions(storage, 111)
        force_clear_live_player_sessions(storage, 222)
        storage.clear_travel(111)
        storage.clear_travel(222)
        assert player_busy_reason(storage, 111, auto_recover=False) is None, player_busy_reason(
            storage, 111, auto_recover=False
        )
        assert player_busy_reason(storage, 222, auto_recover=False) is None, player_busy_reason(
            storage, 222, auto_recover=False
        )
        p111 = storage.get_character(111, refresh_energy=False)
        assert p111 is not None
        storage.set_location(222, p111.location)
        storage.change_money(111, 5000)
        money_before_222 = storage.get_character(222, refresh_energy=False).money
        offer = create_trade_offer(storage, 111, 222, kind="money", amount=250)
        assert offer.ok, offer.text
        assert get_trade_offer(storage, 111) is not None
        tokb = trade_offer_keyboard(111)
        assert any(
            (b.callback_data or "").startswith("trade:accept:")
            for r in tokb.inline_keyboard
            for b in r
        )
        accepted = accept_trade_offer(storage, 111, 222)
        assert accepted.ok, accepted.text
        assert storage.get_character(222, refresh_energy=False).money == money_before_222 + 250
        assert get_trade_offer(storage, 111) is None

        storage.add_item(111, "vodka", 2)
        offer2 = create_trade_offer(storage, 111, 222, kind="item", item_key="vodka", amount=1)
        assert offer2.ok, offer2.text
        declined = decline_trade_offer(storage, 111, 222)
        assert declined.ok, declined.text
        assert get_trade_offer(storage, 111) is None
        assert int(storage.get_character(111, refresh_energy=False).inventory.get("vodka", 0)) >= 2

        from app.ephemeral_messages import (
            EPHEMERAL_QUEUE_META,
            schedule_battle_message_deletion,
            schedule_message_deletion,
        )

        schedule_battle_message_deletion(storage, 111, 4242)
        schedule_message_deletion(storage, -1003958853707, 777, ttl_seconds=120, kind="help_thanks")
        raw_queue = storage.get_meta(EPHEMERAL_QUEUE_META)
        assert raw_queue and "4242" in raw_queue and "777" in raw_queue


if __name__ == "__main__":
    run_smoke_check()
    print("Smoke check passed.")
