"""Settings, from config.toml. Every setting has a default that works with Athena's own
defaults, so the board runs without a config file at all."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = "config.toml"


class ConfigError(Exception):
    """config.toml can't be used. The message says why."""


@dataclass
class Config:
    athena_url: str = "http://127.0.0.1:8091"  # Athena's status API: its [display] host and port
    token: str = ""  # only if Athena's [display] has a token
    led: bool = True  # blue while Athena works, flashing amber when it needs you
    name: str = ""  # the name on the board; "" uses Athena's
    timezone: str = ""  # for the clock; "" uses Athena's
    wait_seconds: float = 25.0  # how long each request lets Athena wait for news (at most 30)
    retry_seconds: float = 5.0  # how soon to try again when Athena doesn't answer


def load_config(path: str | Path | None = None) -> Config:
    """The settings in ``path`` (default: config.toml here). A missing default file means all defaults."""
    file = Path(path or DEFAULT_PATH)
    if not file.exists():
        if path:
            raise ConfigError(f"There's no config file at {file}.")
        return Config()
    try:
        raw = tomllib.loads(file.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{file} isn't valid TOML: {exc}") from None
    fields = {f.name: f for f in dataclasses.fields(Config)}
    unknown = sorted(set(raw) - set(fields))
    if unknown:
        raise ConfigError(f"{file} has settings this version doesn't know: {', '.join(unknown)}")
    for name, value in raw.items():
        expected = type(getattr(Config(), name))
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            raw[name] = float(value)
        elif not isinstance(value, expected) or (expected is not bool and isinstance(value, bool)):
            raise ConfigError(f"In {file}, {name} should be a {expected.__name__}, not {value!r}")
    cfg = Config(**raw)
    if not cfg.athena_url.startswith(("http://", "https://")):
        raise ConfigError(f"In {file}, athena_url should start with http:// or https://")
    return cfg
