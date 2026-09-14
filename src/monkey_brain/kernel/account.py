"""Actor Account — Account and preference metadata for actors.

Provides structured account information including:
- Account status, subscription, login history
- Preferences (theme, notifications, communication style)
- Interests, accessibility settings
- Behavioral metadata

Usage:
    from src.monkey_brain.kernel.account import Account, Preferences, Behavior

    account = Account(
        username="alice",
        email="alice@example.com",
        subscription="pro",
        preferences=Preferences(
            theme="dark",
            notification_settings={"email": True, "push": False},
        ),
    )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("agentos.account")


# ═══════════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════════


class AccountStatus(str, Enum):
    """Account status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"
    PENDING = "pending"
    BANNED = "banned"


class SubscriptionPlan(str, Enum):
    """Subscription plans."""

    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    CUSTOM = "custom"


class DeviceType(str, Enum):
    """Device types."""

    DESKTOP = "desktop"
    LAPTOP = "laptop"
    TABLET = "tablet"
    MOBILE = "mobile"
    SMART_TV = "smart_tv"
    WEARABLE = "wearable"
    IoT = "iot"
    SERVER = "server"


class CommunicationStyle(str, Enum):
    """Preferred communication styles."""

    FORMAL = "formal"
    INFORMAL = "informal"
    TECHNICAL = "technical"
    CASUAL = "casual"
    CONCISE = "concise"
    DETAILED = "detailed"
    VISUAL = "visual"
    AURAL = "aural"


class Theme(str, Enum):
    """UI themes."""

    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"
    HIGH_CONTRAST = "high_contrast"


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class LoginEvent:
    """A single login event."""

    timestamp: float = field(default_factory=time.time)
    device_type: DeviceType = DeviceType.DESKTOP
    ip_address: str = ""
    location: str = ""
    success: bool = True
    method: str = "password"  # password, sso, oauth, biometric

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "device_type": self.device_type.value,
            "ip_address": self.ip_address,
            "location": self.location,
            "success": self.success,
            "method": self.method,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoginEvent:
        return cls(
            timestamp=data.get("timestamp", time.time()),
            device_type=DeviceType(data.get("device_type", "desktop")),
            ip_address=data.get("ip_address", ""),
            location=data.get("location", ""),
            success=data.get("success", True),
            method=data.get("method", "password"),
        )


@dataclass
class NotificationSettings:
    """Notification preferences."""

    email_enabled: bool = True
    push_enabled: bool = True
    sms_enabled: bool = False
    in_app_enabled: bool = True
    frequency: str = "real_time"  # real_time, daily, weekly, never
    quiet_hours_start: str = ""  # HH:MM
    quiet_hours_end: str = ""  # HH:MM
    categories: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "email_enabled": self.email_enabled,
            "push_enabled": self.push_enabled,
            "sms_enabled": self.sms_enabled,
            "in_app_enabled": self.in_app_enabled,
            "frequency": self.frequency,
            "quiet_hours_start": self.quiet_hours_start,
            "quiet_hours_end": self.quiet_hours_end,
            "categories": dict(self.categories),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NotificationSettings:
        return cls(
            email_enabled=data.get("email_enabled", True),
            push_enabled=data.get("push_enabled", True),
            sms_enabled=data.get("sms_enabled", False),
            in_app_enabled=data.get("in_app_enabled", True),
            frequency=data.get("frequency", "real_time"),
            quiet_hours_start=data.get("quiet_hours_start", ""),
            quiet_hours_end=data.get("quiet_hours_end", ""),
            categories=data.get("categories", {}),
        )


@dataclass
class AccessibilitySettings:
    """Accessibility preferences."""

    screen_reader: bool = False
    high_contrast: bool = False
    large_text: bool = False
    reduced_motion: bool = False
    keyboard_navigation: bool = True
    captions_enabled: bool = False
    audio_descriptions: bool = False
    color_blind_mode: str = ""  # protanopia, deuteranopia, tritanopia
    font_size: str = "medium"  # small, medium, large, xlarge
    line_spacing: float = 1.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_reader": self.screen_reader,
            "high_contrast": self.high_contrast,
            "large_text": self.large_text,
            "reduced_motion": self.reduced_motion,
            "keyboard_navigation": self.keyboard_navigation,
            "captions_enabled": self.captions_enabled,
            "audio_descriptions": self.audio_descriptions,
            "color_blind_mode": self.color_blind_mode,
            "font_size": self.font_size,
            "line_spacing": self.line_spacing,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccessibilitySettings:
        return cls(
            screen_reader=data.get("screen_reader", False),
            high_contrast=data.get("high_contrast", False),
            large_text=data.get("large_text", False),
            reduced_motion=data.get("reduced_motion", False),
            keyboard_navigation=data.get("keyboard_navigation", True),
            captions_enabled=data.get("captions_enabled", False),
            audio_descriptions=data.get("audio_descriptions", False),
            color_blind_mode=data.get("color_blind_mode", ""),
            font_size=data.get("font_size", "medium"),
            line_spacing=data.get("line_spacing", 1.5),
        )


@dataclass
class Preferences:
    """User preferences."""

    theme: Theme = Theme.SYSTEM
    language: str = "en"
    timezone: str = "UTC"
    date_format: str = "YYYY-MM-DD"
    time_format: str = "24h"  # 12h, 24h
    communication_style: CommunicationStyle = CommunicationStyle.CONCISE
    notification_settings: NotificationSettings = field(default_factory=NotificationSettings)
    accessibility: AccessibilitySettings = field(default_factory=AccessibilitySettings)
    saved_interests: list[str] = field(default_factory=list)
    custom: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme": self.theme.value,
            "language": self.language,
            "timezone": self.timezone,
            "date_format": self.date_format,
            "time_format": self.time_format,
            "communication_style": self.communication_style.value,
            "notification_settings": self.notification_settings.to_dict(),
            "accessibility": self.accessibility.to_dict(),
            "saved_interests": list(self.saved_interests),
            "custom": dict(self.custom),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Preferences:
        return cls(
            theme=Theme(data.get("theme", "system")),
            language=data.get("language", "en"),
            timezone=data.get("timezone", "UTC"),
            date_format=data.get("date_format", "YYYY-MM-DD"),
            time_format=data.get("time_format", "24h"),
            communication_style=CommunicationStyle(data.get("communication_style", "concise")),
            notification_settings=NotificationSettings.from_dict(data.get("notification_settings", {})),
            accessibility=AccessibilitySettings.from_dict(data.get("accessibility", {})),
            saved_interests=data.get("saved_interests", []),
            custom=data.get("custom", {}),
        )


@dataclass
class Behavior:
    """Behavioral metadata."""

    session_count: int = 0
    total_time_spent: float = 0.0  # seconds
    last_active: float = 0.0
    average_session_length: float = 0.0  # seconds
    preferred_features: list[str] = field(default_factory=list)
    interaction_patterns: dict[str, int] = field(default_factory=dict)
    response_time_avg: float = 0.0  # seconds
    engagement_score: float = 0.5  # 0.0 to 1.0
    satisfaction_score: float = 0.5  # 0.0 to 1.0
    learning_style: str = ""  # visual, auditory, kinesthetic, reading
    peak_activity_hours: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_count": self.session_count,
            "total_time_spent": self.total_time_spent,
            "last_active": self.last_active,
            "average_session_length": self.average_session_length,
            "preferred_features": list(self.preferred_features),
            "interaction_patterns": dict(self.interaction_patterns),
            "response_time_avg": self.response_time_avg,
            "engagement_score": self.engagement_score,
            "satisfaction_score": self.satisfaction_score,
            "learning_style": self.learning_style,
            "peak_activity_hours": list(self.peak_activity_hours),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Behavior:
        return cls(
            session_count=data.get("session_count", 0),
            total_time_spent=data.get("total_time_spent", 0.0),
            last_active=data.get("last_active", 0.0),
            average_session_length=data.get("average_session_length", 0.0),
            preferred_features=data.get("preferred_features", []),
            interaction_patterns=data.get("interaction_patterns", {}),
            response_time_avg=data.get("response_time_avg", 0.0),
            engagement_score=data.get("engagement_score", 0.5),
            satisfaction_score=data.get("satisfaction_score", 0.5),
            learning_style=data.get("learning_style", ""),
            peak_activity_hours=data.get("peak_activity_hours", []),
        )


@dataclass
class Account:
    """Actor account metadata.

    Attributes:
        username: Unique username
        email: Email address
        status: Account status
        subscription: Subscription plan
        created_at: Account creation timestamp
        updated_at: Last update timestamp
        login_history: List of login events
        devices_used: Set of device types used
        preferences: User preferences
        behavior: Behavioral metadata
        metadata: Additional custom fields
    """

    username: str = ""
    email: str = ""
    status: AccountStatus = AccountStatus.ACTIVE
    subscription: SubscriptionPlan = SubscriptionPlan.FREE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    login_history: list[LoginEvent] = field(default_factory=list)
    devices_used: set[DeviceType] = field(default_factory=set)
    preferences: Preferences = field(default_factory=Preferences)
    behavior: Behavior = field(default_factory=Behavior)
    metadata: dict[str, Any] = field(default_factory=dict)

    def update(self, **kwargs: Any) -> None:
        """Update account fields."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.updated_at = time.time()

    def record_login(
        self,
        device_type: DeviceType = DeviceType.DESKTOP,
        ip_address: str = "",
        location: str = "",
        success: bool = True,
        method: str = "password",
    ) -> LoginEvent:
        """Record a login event."""
        event = LoginEvent(
            device_type=device_type,
            ip_address=ip_address,
            location=location,
            success=success,
            method=method,
        )
        self.login_history.append(event)
        self.devices_used.add(device_type)
        self.behavior.session_count += 1
        self.behavior.last_active = time.time()
        return event

    @property
    def last_login(self) -> LoginEvent | None:
        """Get the most recent successful login."""
        successful = [e for e in self.login_history if e.success]
        return successful[-1] if successful else None

    @property
    def login_count(self) -> int:
        """Total number of successful logins."""
        return sum(1 for e in self.login_history if e.success)

    @property
    def failed_login_count(self) -> int:
        """Total number of failed logins."""
        return sum(1 for e in self.login_history if not e.success)

    @property
    def is_active(self) -> bool:
        """Check if account is active."""
        return self.status == AccountStatus.ACTIVE

    @property
    def is_premium(self) -> bool:
        """Check if account has premium subscription."""
        return self.subscription in (SubscriptionPlan.PRO, SubscriptionPlan.ENTERPRISE)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "username": self.username,
            "email": self.email,
            "status": self.status.value,
            "subscription": self.subscription.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "login_history": [e.to_dict() for e in self.login_history],
            "devices_used": [d.value for d in self.devices_used],
            "preferences": self.preferences.to_dict(),
            "behavior": self.behavior.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Account:
        """Deserialize from dict."""
        return cls(
            username=data.get("username", ""),
            email=data.get("email", ""),
            status=AccountStatus(data.get("status", "active")),
            subscription=SubscriptionPlan(data.get("subscription", "free")),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            login_history=[LoginEvent.from_dict(e) for e in data.get("login_history", [])],
            devices_used={DeviceType(d) for d in data.get("devices_used", [])},
            preferences=Preferences.from_dict(data.get("preferences", {})),
            behavior=Behavior.from_dict(data.get("behavior", {})),
            metadata=data.get("metadata", {}),
        )

    def __repr__(self) -> str:
        return f"Account(username={self.username!r}, status={self.status.value!r})"
