"""Command line: `pi-display-microservice [run | demo | check]`."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import Callable

from pi_display_microservice import __version__
from pi_display_microservice.album import PhotoAlbum, explain, read_album
from pi_display_microservice.app import PHOTOS, STATUS, Boards, demo, run_board
from pi_display_microservice.client import AthenaClient
from pi_display_microservice.config import Config, ConfigError, data_dir, load_config
from pi_display_microservice.scores import Scores
from pi_display_microservice.screen import DisplayError, DisplayHATMini, PreviewScreen, Screen
from pi_display_microservice.slideshow import Slideshow
from pi_display_microservice.status import Status


def show(
    cfg: Config, *, demo_mode: bool = False, preview: str | None = None, once: bool = False, page: str | None = None
) -> int:
    if page == PHOTOS and not cfg.album_url:
        print("There are no photos to show: set album_url in config.toml first.", file=sys.stderr)
        return 2
    client = None
    read: Callable[[], Status]
    if demo_mode:
        read = demo()
    else:
        client = AthenaClient(cfg.athena_url, cfg.token, wait=min(cfg.wait_seconds, 30), retry=cfg.retry_seconds)
        if once:
            client.refresh()
        else:
            client.start()
        read = client.latest
    album, slideshow = None, None
    if cfg.album_url and not demo_mode:
        album = PhotoAlbum(cfg.album_url, data_dir() / "photos")
        if once:
            album.refresh()
        else:
            album.start()
        slideshow = Slideshow(album, Scores(data_dir() / "scores.json"))
    try:
        screen: Screen = PreviewScreen(preview) if preview else DisplayHATMini()
    except DisplayError as exc:
        print(exc, file=sys.stderr)
        return 1
    # `systemctl stop` sends SIGTERM. Leave through the normal path so the screen gets cleared.
    previous = signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    what = "examples" if demo_mode else f"Athena's status from {cfg.athena_url}"
    if slideshow:
        what += ", and the photos from album_url"
    logging.getLogger(__name__).info("Showing %s%s", what, f", drawing into {preview}" if preview else "")
    try:
        run_board(Boards(cfg), read, screen, slideshow=slideshow, page=page, led=cfg.led, once=once)
    finally:
        signal.signal(signal.SIGTERM, previous)
        screen.close()
        if client:
            client.stop()
        if album:
            album.stop()
    return 0


def check(cfg: Config) -> int:
    """Ask Athena once, and say what it answered. Then the same for the photo album, if there is one."""
    answered = check_athena(cfg)
    if cfg.album_url:
        answered = check_album(cfg) and answered
    return 0 if answered else 1


def check_athena(cfg: Config) -> bool:
    client = AthenaClient(cfg.athena_url, cfg.token)
    answered = client.refresh()
    status = client.latest()
    s = status.snapshot
    if answered:
        doing = f": {s.step or 'working'}" if s.step else ""
        print(f"Athena answered from {client.url}. {status.name} is {s.state}{doing}.")
        return True
    print(f"No status from {client.url}.", file=sys.stderr)
    print(
        s.problem or "Nothing answered. Is Athena running, with [display] enabled in its config.toml?", file=sys.stderr
    )
    return False


def check_album(cfg: Config) -> bool:
    try:
        listing = read_album(cfg.album_url)
    except Exception as exc:
        print("Couldn't read the photo album at album_url.", file=sys.stderr)
        print(explain(exc), file=sys.stderr)
        return False
    album = f'The album "{listing.title}"' if listing.title else "The album"
    count = f"{len(listing.photos)} photo{'' if len(listing.photos) == 1 else 's'}"
    if listing.complete:
        print(f"{album} has {count}.")
    else:
        print(f"{album} has more photos than its page lists, so the slideshow only has the first {count}.")
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pi-display-microservice", description="Athena's status board on a Display HAT Mini."
    )
    parser.add_argument("--config", help="the settings file (default: config.toml here, if there is one)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    for name, help in [("run", "show Athena's status and photos (the default)"), ("demo", "cycle through examples")]:
        command = sub.add_parser(name, help=help)
        command.add_argument("--preview", metavar="PNG", help="draw into this PNG file instead of on the screen")
        command.add_argument("--once", action="store_true", help="draw one frame, then stop")
        if name == "run":
            command.add_argument(
                "--page", choices=[PHOTOS, STATUS], help="the page to start on (default: the photos, if there are any)"
            )
    sub.add_parser("check", help="ask Athena, and the photo album, once and say what they answered")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
    if args.command == "check":
        sys.exit(check(cfg))
    preview, once, page = getattr(args, "preview", None), getattr(args, "once", False), getattr(args, "page", None)
    try:
        sys.exit(show(cfg, demo_mode=args.command == "demo", preview=preview, once=once, page=page))
    except KeyboardInterrupt:
        sys.exit(130)
