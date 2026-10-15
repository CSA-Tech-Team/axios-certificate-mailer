"""Configuration: defaults, optional JSON file, then MAILQ_* environment overrides.

The SMTP password is read from the environment only (MAILQ_SMTP_PASSWORD).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from datetime import timedelta, timezone, tzinfo
from pathlib import Path


class ConfigError(Exception):
    pass


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "mail-queue"
    return Path.home() / ".local" / "share" / "mail-queue"


@dataclass
class Config:
    data_dir: Path = field(default_factory=default_data_dir)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = "techteam.csa@psgtech.ac.in"
    smtp_password: str = ""
    from_name: str = ""
    reply_to: str = ""
    list_unsubscribe: str = ""
    email_column: str = "email"
    delimiter: str = ","
    daily_cap: int = 1800
    rate_per_sec: float = 0.5
    max_attempts: int = 5
    breaker_n: int = 10
    cooldown_s: float = 900.0
    max_rate_pauses: int = 3
    retry_wait_limit_s: float = 1800.0
    csv_threshold: int = 5000
    timezone: str = "Asia/Kolkata"
    backoff_base_s: float = 60.0
    backoff_cap_s: float = 3600.0
    smtp_timeout_s: float = 30.0
    reconnect_attempts: int = 3
    reconnect_delay_s: float = 5.0

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "queue.db"

    @property
    def lock_path(self) -> Path:
        return Path(self.data_dir) / "send.lock"

    @property
    def log_path(self) -> Path:
        return Path(self.data_dir) / "logs" / "mailq.log"

    @property
    def rejects_dir(self) -> Path:
        return Path(self.data_dir) / "rejects"

    def tz(self) -> tzinfo:
        return get_tz(self.timezone)

    def validate(self) -> None:
        if self.daily_cap <= 0:
            raise ConfigError("daily_cap must be positive")
        if self.rate_per_sec <= 0:
            raise ConfigError("rate_per_sec must be positive")
        if self.max_attempts < 1 or self.breaker_n < 1 or self.max_rate_pauses < 1:
            raise ConfigError("max_attempts, breaker_n and max_rate_pauses must be >= 1")
        if len(self.delimiter) != 1:
            raise ConfigError("delimiter must be a single character")
        self.tz()


def get_tz(name: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        if name in ("Asia/Kolkata", "Asia/Calcutta", "IST"):
            return timezone(timedelta(hours=5, minutes=30), "IST")
        raise ConfigError(f"unknown timezone {name!r} (install the 'tzdata' package?)")


def _coerce(default, raw: str):
    if isinstance(default, bool):
        return raw.lower() in ("1", "true", "yes")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, Path):
        return Path(raw)
    return raw


def load_config(config_file: str | None = None, overrides: dict | None = None) -> Config:
    cfg = Config()
    path = config_file or os.environ.get("MAILQ_CONFIG")
    if path:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot read config file {path}: {exc}")
        known = {f.name for f in fields(Config)}
        for key, value in data.items():
            if key == "smtp_password":
                raise ConfigError("smtp_password must be set via MAILQ_SMTP_PASSWORD, not the config file")
            if key not in known:
                raise ConfigError(f"unknown config key {key!r}")
            setattr(cfg, key, _coerce(getattr(cfg, key), str(value)))
    for f in fields(Config):
        raw = os.environ.get("MAILQ_" + f.name.upper())
        if raw is not None:
            try:
                setattr(cfg, f.name, _coerce(getattr(cfg, f.name), raw))
            except ValueError:
                raise ConfigError(f"invalid value for MAILQ_{f.name.upper()}: {raw!r}")
    for key, value in (overrides or {}).items():
        if value is not None:
            setattr(cfg, key, _coerce(getattr(cfg, key), str(value)))
    cfg.data_dir = Path(cfg.data_dir)
    cfg.validate()
    return cfg
