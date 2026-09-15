from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Preference


class PreferenceService:
    def __init__(self, session: Session):
        self.session = session

    def set(self, owner_id: str, key: str, value: str) -> Preference:
        pref = self.session.scalar(
            select(Preference).where(Preference.owner_id == owner_id, Preference.key == key)
        )
        if pref:
            pref.value = value
        else:
            pref = Preference(owner_id=owner_id, key=key, value=value)
            self.session.add(pref)
        self.session.flush()
        return pref

    def get(self, owner_id: str, key: str) -> str | None:
        pref = self.session.scalar(
            select(Preference).where(Preference.owner_id == owner_id, Preference.key == key)
        )
        return pref.value if pref else None

    def all(self, owner_id: str) -> dict[str, str]:
        prefs = self.session.scalars(select(Preference).where(Preference.owner_id == owner_id))
        return {p.key: p.value for p in prefs}
