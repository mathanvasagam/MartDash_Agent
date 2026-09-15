import logging

from app.bot.telegram import TelegramStoreBot
from app.config import get_settings

# Import models so SQLAlchemy registers all tables before create_all.
from app.db import models as _models  # noqa: F401
from app.db.base import Base
from app.db.session import engine


def main() -> None:
    settings = get_settings()
    settings.validate_runtime()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    Base.metadata.create_all(bind=engine)
    logging.getLogger(__name__).info("Starting Tele Agent with model %s", settings.groq_model)
    TelegramStoreBot().run()


if __name__ == "__main__":
    main()
