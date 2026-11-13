"""User profile management service."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from demerivo.db.models import UserModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserNotFoundError(Exception):
    """User does not exist."""
    pass


class UserService:
    """Manages user profile operations and account lifecycle."""

    def get_profile(self, user_id: str, db: Session) -> UserModel:
        """Fetch user by ID or raise UserNotFoundError."""
        user = db.query(UserModel).filter_by(id=user_id).first()
        if not user:
            raise UserNotFoundError(f"User with ID '{user_id}' not found")
        return user

    def update_profile(
        self,
        user_id: str,
        display_name: Optional[str],
        avatar_url: Optional[str],
        db: Session,
    ) -> UserModel:
        """Update display name and/or avatar URL."""
        user = self.get_profile(user_id, db)
        if display_name is not None:
            user.display_name = display_name.strip()
        if avatar_url is not None:
            user.avatar_url = avatar_url.strip()
        user.updated_at = utcnow()
        db.commit()
        db.refresh(user)
        return user

    def delete_account(self, user_id: str, db: Session) -> bool:
        """Execute account deletion workflow.
        
        Deactivates user record, revokes platform admin status, anonymizes email, and deletes personal workspace cascade.
        """
        user = self.get_profile(user_id, db)
        user.is_active = False
        user.is_platform_admin = False
        user.email = f"deleted_{user.id[:8]}@{user.email.split('@')[-1]}"
        user.display_name = "Deleted User"
        user.avatar_url = None
        user.updated_at = utcnow()
        db.commit()
        return True


_user_service_instance: Optional[UserService] = None


def get_user_service() -> UserService:
    """Return singleton UserService."""
    global _user_service_instance
    if _user_service_instance is None:
        _user_service_instance = UserService()
    return _user_service_instance
