from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable
from utils import get_user


class BanCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id
        user = get_user(user_id)

        # Если пользователь существует и забанен, и при этом он не админ
        if user and user['is_banned'] and user['role'] != 'admin':
            if isinstance(event, Message):
                await event.answer("⛔ Вы забанены и не можете использовать бота.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Вы забанены", show_alert=True)
            return  # Прерываем выполнение хендлера

        # Если не забанен, передаём управление дальше
        return await handler(event, data)
