"""Email service abstraction and cryptographic token management.

Provides:
1. Cryptographically secure email verification tokens (single-use, account-bound, expiring, SHA-256 hashed).
2. Email provider abstraction with Console, Mock, SMTP, and No-Op implementations.
3. Strict guarantee: never pretends emails are sent when no provider is configured.
"""

import abc
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Optional
import smtplib
from sqlalchemy.orm import Session

from demerivo.config import Settings, get_settings
from demerivo.db.models import EmailVerificationTokenModel, UserModel, utcnow

logger = logging.getLogger("demerivo.email")


class EmailDeliveryError(Exception):
    """Raised when email transmission fails."""
    pass


class InvalidVerificationTokenError(Exception):
    """Raised when verification token is invalid, expired, already used, or account-mismatched."""
    pass


# =============================================================================
# Cryptographic Token Management
# =============================================================================

def hash_token(raw_token: str) -> str:
    """Compute constant SHA-256 hash of a verification or reset token."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


def create_verification_token(
    user_id: str,
    email: str,
    db: Session,
    expires_in_hours: int = 24,
) -> str:
    """Generate a high-entropy cryptographically secure single-use verification token.
    
    Stores SHA-256 digest in database with strict account binding and expiry timestamp.
    Returns the unhashed secret token for delivery to customer email.
    """
    raw_token = secrets.token_urlsafe(32)
    token_digest = hash_token(raw_token)
    norm_email = email.strip().lower()

    # Invalidate any previously unused tokens for this user/email to prevent multi-token proliferation
    db.query(EmailVerificationTokenModel).filter(
        EmailVerificationTokenModel.user_id == user_id,
        EmailVerificationTokenModel.email == norm_email,
        EmailVerificationTokenModel.used_at == None,
    ).update({"used_at": utcnow()})

    record = EmailVerificationTokenModel(
        user_id=user_id,
        email=norm_email,
        token_hash=token_digest,
        expires_at=utcnow() + timedelta(hours=expires_in_hours),
        used_at=None,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return raw_token


def verify_email_with_token(
    raw_token: str,
    email: Optional[str],
    db: Session,
) -> UserModel:
    """Validate token cryptographic integrity, expiry, single-use, and account-binding.
    
    Marks token as consumed and sets user.email_verified = True.
    Raises InvalidVerificationTokenError on any discrepancy.
    """
    if not raw_token or len(raw_token.strip()) < 16:
        raise InvalidVerificationTokenError("Verification token is malformed or missing.")

    token_digest = hash_token(raw_token)
    token_record = (
        db.query(EmailVerificationTokenModel)
        .filter_by(token_hash=token_digest)
        .first()
    )

    if not token_record:
        raise InvalidVerificationTokenError("Verification token was not found or is invalid.")

    # 1. Single-use enforcement
    if token_record.used_at is not None:
        raise InvalidVerificationTokenError("Verification token has already been consumed.")

    # 2. Expiration check
    expires_at = token_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    now = utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise InvalidVerificationTokenError("Verification token has expired. Please request a new verification link.")

    # 3. Account binding check (cannot verify arbitrary email with another account's token)
    if email:
        norm_email = email.strip().lower()
        if token_record.email.lower() != norm_email:
            raise InvalidVerificationTokenError("Token does not correspond to the specified email address.")

    user = db.query(UserModel).filter_by(id=token_record.user_id).first()
    if not user:
        raise InvalidVerificationTokenError("User account associated with this token no longer exists.")

    # Mark token as consumed
    token_record.used_at = utcnow()
    user.email_verified = True
    user.updated_at = utcnow()

    db.commit()
    db.refresh(user)
    return user


# =============================================================================
# Email Provider Abstraction
# =============================================================================

class EmailProvider(abc.ABC):
    """Abstract interface for customer transaction emails."""

    @abc.abstractmethod
    def send_verification_email(self, to_email: str, token: str, user_name: Optional[str] = None) -> bool:
        """Send account email verification link."""
        pass

    @abc.abstractmethod
    def send_password_reset_email(self, to_email: str, token: str) -> bool:
        """Send password reset instructions."""
        pass

    @abc.abstractmethod
    def send_notification(self, to_email: str, subject: str, message: str) -> bool:
        """Send general transactional notification."""
        pass


class NoOpEmailProvider(EmailProvider):
    """Null provider used when email delivery is not configured.
    
    Explicitly refuses to pretend emails were dispatched.
    """
    def send_verification_email(self, to_email: str, token: str, user_name: Optional[str] = None) -> bool:
        logger.warning("Email provider not configured. Verification email to %s was not sent.", to_email)
        return False

    def send_password_reset_email(self, to_email: str, token: str) -> bool:
        logger.warning("Email provider not configured. Password reset email to %s was not sent.", to_email)
        return False

    def send_notification(self, to_email: str, subject: str, message: str) -> bool:
        logger.warning("Email provider not configured. Notification to %s was not sent.", to_email)
        return False


class ConsoleEmailProvider(EmailProvider):
    """Local development console provider.
    
    Logs sanitized transmission notice without exposing raw secret tokens in output.
    """
    def send_verification_email(self, to_email: str, token: str, user_name: Optional[str] = None) -> bool:
        logger.info("[ConsoleEmailProvider] Verification email dispatched to %s (token length: %d)", to_email, len(token))
        return True

    def send_password_reset_email(self, to_email: str, token: str) -> bool:
        logger.info("[ConsoleEmailProvider] Password reset email dispatched to %s (token length: %d)", to_email, len(token))
        return True

    def send_notification(self, to_email: str, subject: str, message: str) -> bool:
        logger.info("[ConsoleEmailProvider] Notification '%s' dispatched to %s", subject, to_email)
        return True


class MockEmailProvider(EmailProvider):
    """In-memory outbox provider for automated test verification."""
    def __init__(self):
        self.outbox: List[Dict[str, Any]] = []

    def send_verification_email(self, to_email: str, token: str, user_name: Optional[str] = None) -> bool:
        self.outbox.append({
            "type": "verification",
            "to": to_email,
            "token": token,
            "user_name": user_name,
            "created_at": utcnow().isoformat(),
        })
        return True

    def send_password_reset_email(self, to_email: str, token: str) -> bool:
        self.outbox.append({
            "type": "password_reset",
            "to": to_email,
            "token": token,
            "created_at": utcnow().isoformat(),
        })
        return True

    def send_notification(self, to_email: str, subject: str, message: str) -> bool:
        self.outbox.append({
            "type": "notification",
            "to": to_email,
            "subject": subject,
            "message": message,
            "created_at": utcnow().isoformat(),
        })
        return True

    def clear(self):
        self.outbox.clear()


class SmtpEmailProvider(EmailProvider):
    """Production SMTP provider connecting via standard smtplib with TLS."""
    def __init__(self, settings: Settings):
        self.settings = settings

    def _send_message(self, to_email: str, subject: str, body: str) -> bool:
        if not self.settings.smtp_host:
            raise EmailDeliveryError("SMTP host is not configured.")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"{self.settings.email_from_name} <{self.settings.email_from_address}>"
        msg["To"] = to_email
        msg.set_content(body)

        try:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10.0) as server:
                if self.settings.smtp_use_tls:
                    server.starttls()
                if self.settings.smtp_user and self.settings.smtp_password:
                    server.login(self.settings.smtp_user, self.settings.smtp_password)
                server.send_message(msg)
            return True
        except Exception as e:
            logger.error("Failed to send SMTP email to %s: %s", to_email, e)
            raise EmailDeliveryError(f"SMTP delivery failed: {str(e)}") from e

    def send_verification_email(self, to_email: str, token: str, user_name: Optional[str] = None) -> bool:
        subject = "Verify your Demerivo PixelFix account"
        verification_link = f"https://demerivo.com/verify-email?token={token}&email={to_email}"
        body = (
            f"Hello {user_name or 'there'},\n\n"
            f"Thank you for registering with Demerivo PixelFix.\n"
            f"Please click the link below to verify your email address:\n\n"
            f"{verification_link}\n\n"
            f"This link expires in 24 hours and can only be used once.\n\n"
            f"Best regards,\nDemerivo PixelFix Team"
        )
        return self._send_message(to_email, subject, body)

    def send_password_reset_email(self, to_email: str, token: str) -> bool:
        subject = "Reset your Demerivo PixelFix password"
        reset_link = f"https://demerivo.com/reset-password?token={token}"
        body = (
            f"Hello,\n\n"
            f"A password reset was requested for your Demerivo PixelFix account.\n"
            f"Click the link below to set a new password:\n\n"
            f"{reset_link}\n\n"
            f"If you did not request this, please ignore this email.\n\n"
            f"Best regards,\nDemerivo PixelFix Team"
        )
        return self._send_message(to_email, subject, body)

    def send_notification(self, to_email: str, subject: str, message: str) -> bool:
        return self._send_message(to_email, subject, message)


_email_provider_instance: Optional[EmailProvider] = None


def get_email_provider(settings: Optional[Settings] = None) -> EmailProvider:
    """Return active email provider singleton based on configuration."""
    global _email_provider_instance
    if _email_provider_instance is None:
        cfg = settings or get_settings()
        prov_type = getattr(cfg, "email_provider", "none").lower()
        if prov_type == "mock":
            _email_provider_instance = MockEmailProvider()
        elif prov_type == "console":
            _email_provider_instance = ConsoleEmailProvider()
        elif prov_type == "smtp":
            _email_provider_instance = SmtpEmailProvider(cfg)
        else:
            _email_provider_instance = NoOpEmailProvider()
    return _email_provider_instance


def set_email_provider(provider: Optional[EmailProvider]):
    """Override singleton (used in test suites)."""
    global _email_provider_instance
    _email_provider_instance = provider
