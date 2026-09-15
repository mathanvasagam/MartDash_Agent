from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.agent.runtime import AgentRuntime
from app.agent.tools import ToolContext
from app.config import get_settings
from app.domain.idempotency import UpdateIdempotencyService

logger = logging.getLogger(__name__)


class TelegramStoreBot:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.runtime = AgentRuntime()
        self.idempotency = UpdateIdempotencyService()
        self.chat_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(
                "Store agent is ready. Talk naturally: receive stock, build a bill, check stock, manage khata, close the day, or request an invoice/deck."
            )

    async def new_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat:
            return
        self.runtime.clear_chat(str(update.effective_chat.id))
        if update.message:
            await update.message.reply_text(
                "Conversation context cleared. Store data, bill records, khata, stock, and saved preferences are unchanged."
            )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(
                "Examples:\n"
                "• 50 packets of Maggi came in, cost ₹12, MRP ₹14\n"
                "• make a bill: 2kg sugar, 4 Maggi, UPI\n"
                "• drop the butter; make it 6 Maggi\n"
                "• finalize the bill\n"
                "• Ramesh paid ₹300\n"
                "• what's running out?\n"
                "• send me the last invoice as PDF\n"
                "• make this week's sales analysis deck"
            )

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (
            not update.message
            or not update.message.text
            or not update.effective_user
            or not update.effective_chat
        ):
            return

        claimed, previous_response = await asyncio.to_thread(
            self.idempotency.claim, update.update_id
        )
        if not claimed:
            if previous_response:
                await self._reply_chunks(update, previous_response)
            return

        chat_id = str(update.effective_chat.id)
        owner_id = str(update.effective_user.id)
        tool_context = ToolContext(owner_id=owner_id, chat_id=chat_id, update_id=update.update_id)

        try:
            async with self.chat_locks[chat_id]:
                response = await asyncio.to_thread(
                    self.runtime.respond, update.message.text, tool_context
                )
            await asyncio.to_thread(self.idempotency.complete, update.update_id, response)
            await self._reply_chunks(update, response)

            for path in tool_context.pending_files:
                with path.open("rb") as file_handle:
                    await update.message.reply_document(document=file_handle, filename=path.name)
        except Exception:
            logger.exception("Failed to process Telegram update %s", update.update_id)
            message = "I could not complete that request safely. No unsupported assumption was applied. Please retry or check the service logs."
            await asyncio.to_thread(self.idempotency.fail, update.update_id, message)
            await self._reply_chunks(update, message)

    @staticmethod
    async def _reply_chunks(update: Update, text: str) -> None:
        if not update.message:
            return
        remaining = text or "Done."
        while remaining:
            chunk = remaining[:4000]
            remaining = remaining[4000:]
            await update.message.reply_text(chunk)

    def run(self) -> None:
        application = (
            Application.builder()
            .token(self.settings.telegram_bot_token)
            .concurrent_updates(8)
            .build()
        )
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("new", self.new_chat))
        application.add_handler(CommandHandler("help", self.help))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
