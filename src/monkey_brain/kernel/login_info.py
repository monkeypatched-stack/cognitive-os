"""Actor LoginInfo — Authentication metadata for actors.

Provides structured login information including:
- Email and password (hashed)
- Mobile number for OTP
- OTP generation and verification
- Password strength validation
- Session management

Usage:
    from src.monkey_brain.kernel.login_info import LoginInfo

    login = LoginInfo(
        email="alice@example.com",
        mobile="+1-555-123-4567",
    )
    login.set_password("secure_password_123")
    login.generate_otp()
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("agentos.login_info")


# ═══════════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════════


class AuthMethod(str, Enum):
    """Authentication methods."""

    PASSWORD = "password"
    OTP = "otp"
    SSO = "sso"
    OAUTH = "oauth"
    BIOMETRIC = "biometric"
    MAGIC_LINK = "magic_link"


class AccountStatus(str, Enum):
    """Account status."""

    ACTIVE = "active"
    UNVERIFIED = "unverified"
    SUSPENDED = "suspended"
    LOCKED = "locked"
    DEACTIVATED = "deactivated"


class OTPStatus(str, Enum):
    """OTP status."""

    PENDING = "pending"
    VERIFIED = "verified"
    EXPIRED = "expired"
    FAILED = "failed"


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class PasswordInfo:
    """Password metadata."""

    password_hash: str = ""
    salt: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    strength: int = 0  # 0-100

    @property
    def is_valid(self) -> bool:
        """Check if password is set and valid."""
        return bool(self.password_hash)

    @property
    def is_expired(self) -> bool:
        """Check if password has expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "password_hash": self.password_hash,
            "salt": self.salt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "strength": self.strength,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PasswordInfo:
        return cls(
            password_hash=data.get("password_hash", ""),
            salt=data.get("salt", ""),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            expires_at=data.get("expires_at"),
            strength=data.get("strength", 0),
        )


@dataclass
class OTPInfo:
    """OTP (One-Time Password) metadata."""

    code: str = ""
    status: OTPStatus = OTPStatus.PENDING
    method: AuthMethod = AuthMethod.OTP
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    attempts: int = 0
    max_attempts: int = 3
    destination: str = ""  # email or mobile
    ip_address: str = ""

    @property
    def is_expired(self) -> bool:
        """Check if OTP has expired."""
        return time.time() > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if OTP is valid and not expired."""
        return self.status == OTPStatus.PENDING and not self.is_expired and self.attempts < self.max_attempts

    def verify(self, code: str) -> bool:
        """Verify OTP code."""
        if not self.is_valid:
            return False

        self.attempts += 1

        if hmac.compare_digest(self.code, code):
            self.status = OTPStatus.VERIFIED
            return True

        if self.attempts >= self.max_attempts:
            self.status = OTPStatus.FAILED

        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status.value,
            "method": self.method.value,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "destination": self.destination,
            "ip_address": self.ip_address,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OTPInfo:
        return cls(
            code=data.get("code", ""),
            status=OTPStatus(data.get("status", "pending")),
            method=AuthMethod(data.get("method", "otp")),
            created_at=data.get("created_at", time.time()),
            expires_at=data.get("expires_at", 0.0),
            attempts=data.get("attempts", 0),
            max_attempts=data.get("max_attempts", 3),
            destination=data.get("destination", ""),
            ip_address=data.get("ip_address", ""),
        )


@dataclass
class SessionInfo:
    """Session metadata."""

    session_id: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    ip_address: str = ""
    user_agent: str = ""
    device_type: str = ""
    location: str = ""
    is_active: bool = True

    @property
    def is_expired(self) -> bool:
        """Check if session has expired."""
        return time.time() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "device_type": self.device_type,
            "location": self.location,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionInfo:
        return cls(
            session_id=data.get("session_id", ""),
            created_at=data.get("created_at", time.time()),
            expires_at=data.get("expires_at", 0.0),
            ip_address=data.get("ip_address", ""),
            user_agent=data.get("user_agent", ""),
            device_type=data.get("device_type", ""),
            location=data.get("location", ""),
            is_active=data.get("is_active", True),
        )


@dataclass
class LoginInfo:
    """Actor login information.

    Attributes:
        email: Email address
        email_verified: Whether email is verified
        mobile: Mobile number (E.164 format)
        mobile_verified: Whether mobile is verified
        password: Password info (hashed)
        auth_methods: Enabled authentication methods
        status: Account status
        otp: Current OTP info
        sessions: Active sessions
        login_attempts: Failed login attempts counter
        locked_until: Account lock expiry timestamp
        last_login: Last successful login timestamp
        metadata: Additional custom fields
    """

    email: str = ""
    email_verified: bool = False
    mobile: str = ""
    mobile_verified: bool = False
    password: PasswordInfo = field(default_factory=PasswordInfo)
    auth_methods: list[AuthMethod] = field(default_factory=lambda: [AuthMethod.PASSWORD])
    status: AccountStatus = AccountStatus.UNVERIFIED
    otp: OTPInfo = field(default_factory=OTPInfo)
    sessions: list[SessionInfo] = field(default_factory=list)
    login_attempts: int = 0
    max_login_attempts: int = 5
    locked_until: float | None = None
    last_login: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Password Operations ─────────────────────────────────────

    def set_password(self, password: str, expires_in_days: int | None = None) -> bool:
        """Set password with hashing.

        Args:
            password: Plain text password
            expires_in_days: Optional expiration in days

        Returns:
            True if password was set successfully
        """
        if not self._validate_password_strength(password):
            logger.warning("Password too weak")
            return False

        salt = secrets.token_hex(16)
        password_hash = self._hash_password(password, salt)

        self.password = PasswordInfo(
            password_hash=password_hash,
            salt=salt,
            strength=self._calculate_strength(password),
            expires_at=(time.time() + (expires_in_days * 86400) if expires_in_days else None),
        )

        # Enable password auth method
        if AuthMethod.PASSWORD not in self.auth_methods:
            self.auth_methods.append(AuthMethod.PASSWORD)

        return True

    def verify_password(self, password: str) -> bool:
        """Verify password.

        Args:
            password: Plain text password to verify

        Returns:
            True if password matches
        """
        if not self.password.is_valid:
            return False

        if self.password.is_expired:
            logger.warning("Password expired")
            return False

        password_hash = self._hash_password(password, self.password.salt)
        return hmac.compare_digest(password_hash, self.password.password_hash)

    def _hash_password(self, password: str, salt: str) -> str:
        """Hash password with salt."""
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000).hex()

    def _validate_password_strength(self, password: str) -> bool:
        """Validate password meets minimum requirements."""
        if len(password) < 8:
            return False
        if not re.search(r"[A-Z]", password):
            return False
        if not re.search(r"[a-z]", password):
            return False
        if not re.search(r"[0-9]", password):
            return False
        return True

    def _calculate_strength(self, password: str) -> int:
        """Calculate password strength (0-100)."""
        strength = 0
        if len(password) >= 8:
            strength += 20
        if len(password) >= 12:
            strength += 10
        if len(password) >= 16:
            strength += 10
        if re.search(r"[A-Z]", password):
            strength += 15
        if re.search(r"[a-z]", password):
            strength += 15
        if re.search(r"[0-9]", password):
            strength += 15
        if re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            strength += 15
        return min(100, strength)

    # ── OTP Operations ──────────────────────────────────────────

    def generate_otp(
        self,
        method: AuthMethod = AuthMethod.OTP,
        length: int = 6,
        expiry_seconds: int = 300,
        destination: str = "",
    ) -> str:
        """Generate OTP for verification.

        Args:
            method: Authentication method (OTP or MOBILE)
            length: OTP length (default 6)
            expiry_seconds: OTP expiry time (default 5 minutes)
            destination: Explicit destination (email or mobile). If not provided,
                        uses mobile if available, otherwise email.

        Returns:
            Generated OTP code
        """
        code = "".join([str(secrets.randbelow(10)) for _ in range(length)])

        # Determine destination
        if not destination:
            if self.mobile:
                destination = self.mobile
            elif self.email:
                destination = self.email

        self.otp = OTPInfo(
            code=code,
            status=OTPStatus.PENDING,
            method=method,
            created_at=time.time(),
            expires_at=time.time() + expiry_seconds,
            destination=destination,
        )

        logger.info(f"OTP generated for {destination}")
        return code

    def verify_otp(self, code: str) -> bool:
        """Verify OTP code.

        Args:
            code: OTP code to verify

        Returns:
            True if OTP is valid
        """
        if not self.otp.is_valid:
            return False

        success = self.otp.verify(code)

        if success:
            # Mark email/mobile as verified
            if self.otp.destination == self.email:
                self.email_verified = True
                if AuthMethod.OTP not in self.auth_methods:
                    self.auth_methods.append(AuthMethod.OTP)
            elif self.otp.destination == self.mobile:
                self.mobile_verified = True

            # Update account status
            if self.status == AccountStatus.UNVERIFIED:
                self.status = AccountStatus.ACTIVE

        return success

    # ── Session Operations ──────────────────────────────────────

    def create_session(
        self,
        ip_address: str = "",
        user_agent: str = "",
        device_type: str = "",
        location: str = "",
        expires_in_hours: int = 24,
    ) -> SessionInfo:
        """Create a new session.

        Args:
            ip_address: Client IP address
            user_agent: Client user agent
            device_type: Device type
            location: Location string
            expires_in_hours: Session expiry (default 24 hours)

        Returns:
            Created session
        """
        session = SessionInfo(
            session_id=secrets.token_hex(32),
            created_at=time.time(),
            expires_at=time.time() + (expires_in_hours * 3600),
            ip_address=ip_address,
            user_agent=user_agent,
            device_type=device_type,
            location=location,
        )
        self.sessions.append(session)
        self.last_login = time.time()
        self.login_attempts = 0

        return session

    def invalidate_session(self, session_id: str) -> bool:
        """Invalidate a session."""
        for session in self.sessions:
            if session.session_id == session_id:
                session.is_active = False
                return True
        return False

    def invalidate_all_sessions(self) -> int:
        """Invalidate all sessions."""
        count = 0
        for session in self.sessions:
            if session.is_active:
                session.is_active = False
                count += 1
        return count

    @property
    def active_sessions(self) -> list[SessionInfo]:
        """Get active, non-expired sessions."""
        return [s for s in self.sessions if s.is_active and not s.is_expired]

    # ── Account Operations ──────────────────────────────────────

    def record_failed_login(self) -> bool:
        """Record a failed login attempt.

        Returns:
            True if account is now locked
        """
        self.login_attempts += 1
        if self.login_attempts >= self.max_login_attempts:
            self.locked_until = time.time() + 1800  # Lock for 30 minutes
            self.status = AccountStatus.LOCKED
            return True
        return False

    def reset_failed_logins(self) -> None:
        """Reset failed login counter."""
        self.login_attempts = 0
        self.locked_until = None

    @property
    def is_locked(self) -> bool:
        """Check if account is locked."""
        if self.locked_until is None:
            return False
        if time.time() > self.locked_until:
            self.locked_until = None
            self.status = AccountStatus.ACTIVE
            return False
        return True

    @property
    def is_authenticated(self) -> bool:
        """Check if account is fully authenticated."""
        return self.status == AccountStatus.ACTIVE and self.email_verified and self.password.is_valid

    @property
    def has_2fa(self) -> bool:
        """Check if 2FA is enabled."""
        return AuthMethod.OTP in self.auth_methods and self.mobile_verified

    # ── Serialization ───────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "email": self.email,
            "email_verified": self.email_verified,
            "mobile": self.mobile,
            "mobile_verified": self.mobile_verified,
            "password": self.password.to_dict(),
            "auth_methods": [m.value for m in self.auth_methods],
            "status": self.status.value,
            "otp": self.otp.to_dict(),
            "sessions": [s.to_dict() for s in self.sessions],
            "login_attempts": self.login_attempts,
            "max_login_attempts": self.max_login_attempts,
            "locked_until": self.locked_until,
            "last_login": self.last_login,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoginInfo:
        """Deserialize from dict."""
        return cls(
            email=data.get("email", ""),
            email_verified=data.get("email_verified", False),
            mobile=data.get("mobile", ""),
            mobile_verified=data.get("mobile_verified", False),
            password=PasswordInfo.from_dict(data.get("password", {})),
            auth_methods=[AuthMethod(m) for m in data.get("auth_methods", ["password"])],
            status=AccountStatus(data.get("status", "unverified")),
            otp=OTPInfo.from_dict(data.get("otp", {})),
            sessions=[SessionInfo.from_dict(s) for s in data.get("sessions", [])],
            login_attempts=data.get("login_attempts", 0),
            max_login_attempts=data.get("max_login_attempts", 5),
            locked_until=data.get("locked_until"),
            last_login=data.get("last_login", 0.0),
            metadata=data.get("metadata", {}),
        )

    def __repr__(self) -> str:
        return f"LoginInfo(email={self.email!r}, status={self.status.value!r})"
