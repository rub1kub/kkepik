"""Manage up to two group subscriptions per user."""

from __future__ import annotations

from difflib import get_close_matches
from html import escape

from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
from schedules.schedule_catalog import get_catalog_values
from tracked_groups import (
    MAX_TRACKED_GROUPS,
    add_tracked_group,
    get_tracked_groups,
    get_user_role,
    is_valid_group_name,
    remove_tracked_group,
)


class GroupManagementStates(StatesGroup):
    waiting_group = State()


def _panel(user_id: int) -> tuple[str, types.InlineKeyboardMarkup]:
    role = get_user_role(config.DB_PATH, user_id)
    groups = get_tracked_groups(config.DB_PATH, user_id)

    lines = ["👥 <b>Отслеживаемые группы</b>", ""]
    if groups:
        lines.extend(
            f"{slot}. <code>{escape(group)}</code>"
            for slot, group in enumerate(groups, 1)
        )
    else:
        lines.append("Пока ни одной группы.")
    lines.extend(["", "Расписание каждой группы придёт отдельным сообщением."])

    buttons = []
    if len(groups) < MAX_TRACKED_GROUPS:
        buttons.append(
            [types.InlineKeyboardButton(text="➕ Добавить группу", callback_data="groups:add")]
        )

    can_remove_last = role == config.ROLE_TEACHER
    for slot, group in enumerate(groups, 1):
        if len(groups) > 1 or can_remove_last:
            buttons.append(
                [
                    types.InlineKeyboardButton(
                        text=f"➖ Удалить {group}",
                        callback_data=f"groups:remove:{slot}",
                    )
                ]
            )

    return "\n".join(lines), types.InlineKeyboardMarkup(inline_keyboard=buttons)


async def cmd_groups(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    if get_user_role(config.DB_PATH, user_id) is None:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return

    await state.clear()
    text, keyboard = _panel(user_id)
    await message.answer(text, reply_markup=keyboard)


async def process_groups_callback(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    user_id = callback.from_user.id
    data = callback.data or ""

    if get_user_role(config.DB_PATH, user_id) is None:
        await callback.answer("Сначала зарегистрируйтесь через /start", show_alert=True)
        return

    if data == "groups:add":
        if len(get_tracked_groups(config.DB_PATH, user_id)) >= MAX_TRACKED_GROUPS:
            await callback.answer("Можно отслеживать не больше двух групп.", show_alert=True)
            return
        await state.set_state(GroupManagementStates.waiting_group)
        await callback.message.answer(
            "Введите номер второй группы, например: <code>103-Д3-3ИНС</code>."
        )
        await callback.answer()
        return

    if data.startswith("groups:remove:"):
        try:
            slot = int(data.rsplit(":", 1)[1])
        except ValueError:
            await callback.answer("Некорректная группа.", show_alert=True)
            return

        result = remove_tracked_group(config.DB_PATH, user_id, slot)
        if result == "last_student_group":
            await callback.answer(
                "Студенту нужна хотя бы одна группа. Для замены используйте /reset.",
                show_alert=True,
            )
            return
        if result != "removed":
            await callback.answer("Группа уже не отслеживается.", show_alert=True)
            return

        text, keyboard = _panel(user_id)
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer("Группа удалена")


async def process_added_group(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Введите номер группы текстом.")
        return

    group = message.text.strip().upper()
    if not is_valid_group_name(group):
        await message.answer(
            "Не похоже на номер группы. Пример: <code>103-Д3-3ИНС</code>."
        )
        return

    available_groups = get_catalog_values("groups")
    if available_groups:
        canonical_groups = {item.upper(): item for item in available_groups}
        canonical_group = canonical_groups.get(group)
        if canonical_group is None:
            suggestions = get_close_matches(
                group,
                list(canonical_groups),
                n=3,
                cutoff=0.55,
            )
            hint = ""
            if suggestions:
                values = "\n".join(
                    f"• <code>{escape(canonical_groups[item])}</code>"
                    for item in suggestions
                )
                hint = f"\n\nВозможно, вы имели в виду:\n{values}"
            await message.answer(
                "Такой группы нет в актуальном списке расписания." + hint
            )
            return
        group = canonical_group

    result = add_tracked_group(config.DB_PATH, message.from_user.id, group)
    if result == "exists":
        await message.answer("Эта группа уже отслеживается.")
        return
    if result == "full":
        await state.clear()
        await message.answer("Можно отслеживать не больше двух групп.")
        return
    if result != "added":
        await state.clear()
        await message.answer("Сначала зарегистрируйтесь: /start")
        return

    await state.clear()
    text, keyboard = _panel(message.from_user.id)
    await message.answer(
        f"✅ Группа <code>{escape(group)}</code> добавлена.\n\n{text}",
        reply_markup=keyboard,
    )
