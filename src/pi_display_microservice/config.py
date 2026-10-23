"""Settings, from config.toml. Every setting has a default that works with Hermes and the
board's plugin for it (hermes_plugin/), so the board runs without a config file at all."""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = "config.toml"


def data_dir() -> Path:
    """Where the board keeps what it downloads and learns: the album's photos, and your likes."""
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "pi-display-microservice"


class ConfigError(Exception):
    """config.toml can't be used. The message says why."""


@dataclass
class Config:
    hermes_url: str = "http://127.0.0.1:8091"  # the board's plugin in Hermes: PI_DISPLAY_HOST and _PORT
    token: str = ""  # only if the plugin has a PI_DISPLAY_TOKEN: the same one
    led: bool = True  # blue while Hermes works, flashing amber when it needs you
    name: str = ""  # the name on the board; "" uses Hermes'
    timezone: str = ""  # for the clock; "" uses this machine's
    wait_seconds: float = 25.0  # how long each request lets Hermes wait for news (at most 30)
    retry_seconds: float = 5.0  # how soon to try again when Hermes doesn't answer
    album_url: str = ""  # a Google Photos album's share link, for the photo page; "" for no photos


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
    if "athena_url" in raw:  # its name from when the board showed Athena
        if "hermes_url" in raw:
            raise ConfigError(f"{file} has both hermes_url and athena_url, its old name. Keep hermes_url.")
        raw["hermes_url"] = raw.pop("athena_url")
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
    if not cfg.hermes_url.startswith(("http://", "https://")):
        raise ConfigError(f"In {file}, hermes_url should start with http:// or https://")
    return cfg
