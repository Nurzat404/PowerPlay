from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable

from razryad_arena_utils import get_user, get_or_create_user, update_user
from keyboards import subscription_required_keyboard


class BanCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id
        user = get_user(user_id)

        if user and user['is_banned'] and user['role'] != 'admin':
            if isinstance(event, Message):
                await event.answer("⛔ Вы забанены и не можете использовать бота.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Вы забанены", show_alert=True)
            return

        return await handler(event, data)


class RequiredSubscriptionMiddleware(BaseMiddleware):
    def __init__(self, channel_username: str):
        super().__init__()
        self.channel_username = (channel_username or 'razryadarena').strip().lstrip('@')
        self.channel_ref = f"@{self.channel_username}"

    async def _is_subscribed(self, bot, telegram_id: int) -> bool:
        try:
            member = await bot.get_chat_member(self.channel_ref, telegram_id)
            status = getattr(member, 'status', None)
            return status in {'member', 'administrator', 'creator'}
        except Exception:
            return False

    def _extract_start_payload(self, event: Message | CallbackQuery) -> str:
        if not isinstance(event, Message):
            return ""
        text = (event.text or "").strip()
        if not text.startswith("/start"):
            return ""
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    def _persist_pending_start_payload(self, event: Message | CallbackQuery) -> None:
        payload = self._extract_start_payload(event)
        if not payload:
            return
        first_name = event.from_user.first_name or ""
        last_name = event.from_user.last_name or ""
        username = event.from_user.username or ""
        user = get_or_create_user(event.from_user.id, first_name, last_name, username)
        if not user or user["email"]:
            return
        if user["pending_start_payload"] == payload:
            return
        update_user(event.from_user.id, pending_start_payload=payload)

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id
        bot = data.get('bot')
        if not bot:
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.data == 'check_subscription':
            return await handler(event, data)

        is_subscribed = await self._is_subscribed(bot, user_id)
        if is_subscribed:
            return await handler(event, data)

        self._persist_pending_start_payload(event)
        kb = subscription_required_keyboard(self.channel_username)
        text = (
            '🔒 Для использования бота нужно подписаться на канал @' + self.channel_username + '\n\n'
            'После подписки нажмите «✅ Проверить подписку».'
        )

        if isinstance(event, Message):
            await event.answer(text, reply_markup=kb)
            return

        if isinstance(event, CallbackQuery):
            await event.answer('Нужна подписка на канал @' + self.channel_username, show_alert=True)
            try:
                await event.message.answer(text, reply_markup=kb)
            except Exception:
                pass
            return

        return await handler(event, data)
