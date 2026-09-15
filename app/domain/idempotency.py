from datetime import UTC, datetime

from app.db.models import ProcessedUpdate
from app.db.session import session_scope


class UpdateIdempotencyService:
    """Durable Telegram update guard. Duplicate update_ids are never executed twice."""

    def claim(self, update_id: int) -> tuple[bool, str | None]:
        with session_scope() as session:
            row = session.get(ProcessedUpdate, update_id)
            if row:
                return False, row.response_text
            session.add(ProcessedUpdate(update_id=update_id, status="PROCESSING"))
            return True, None

    def complete(self, update_id: int, response_text: str) -> None:
        with session_scope() as session:
            row = session.get(ProcessedUpdate, update_id)
            if not row:
                row = ProcessedUpdate(update_id=update_id)
                session.add(row)
            row.status = "PROCESSED"
            row.response_text = response_text
            row.processed_at = datetime.now(UTC)

    def fail(self, update_id: int, response_text: str) -> None:
        with session_scope() as session:
            row = session.get(ProcessedUpdate, update_id)
            if row:
                row.status = "FAILED"
                row.response_text = response_text
                row.processed_at = datetime.now(UTC)
