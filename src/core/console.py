"""Minimal, dependency-free terminal formatting for the demo CLI.

Colour is emitted only when stdout is a real TTY and NO_COLOR is unset, so piped
or redirected output stays clean and diffable.
"""

from __future__ import annotations

import os
import sys

_ENABLED = sys.stdout.isatty() and not os.getenv("NO_COLOR")

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
}

WIDTH = 74


def paint(text: str, *styles: str) -> str:
    if not _ENABLED or not styles:
        return text
    prefix = "".join(_CODES.get(s, "") for s in styles)
    return f"{prefix}{text}{_CODES['reset']}"


def banner(title: str, subtitle: str = "") -> None:
    print()
    print(paint("=" * WIDTH, "cyan"))
    print(paint(f" {title}", "bold", "cyan"))
    if subtitle:
        print(paint(f" {subtitle}", "dim"))
    print(paint("=" * WIDTH, "cyan"))


def step(index: int, total: int, title: str) -> None:
    print()
    print(paint(f"[{index}/{total}] {title}", "bold", "blue"))


def field(label: str, value: object, indent: int = 6) -> None:
    print(f"{' ' * indent}{label:<26}{value}")


def ok(message: str, indent: int = 6) -> None:
    print(f"{' ' * indent}{paint('OK', 'green', 'bold')}   {message}")


def warn(message: str, indent: int = 6) -> None:
    print(f"{' ' * indent}{paint('WARN', 'yellow', 'bold')} {message}")


def fail(message: str, indent: int = 6) -> None:
    print(f"{' ' * indent}{paint('FAIL', 'red', 'bold')} {message}")


def note(message: str, indent: int = 6) -> None:
    print(f"{' ' * indent}{paint(message, 'dim')}")


def rule() -> None:
    print(paint("-" * WIDTH, "dim"))


def status(text: str, good: bool) -> None:
    print()
    print(paint("=" * WIDTH, "cyan"))
    colour = "green" if good else "red"
    print(paint(f" STATUS: {text}", "bold", colour))
    print(paint("=" * WIDTH, "cyan"))
    print()


def error_block(exc: Exception, code: str = "", remedy: str = "") -> None:
    """Render a typed pipeline error consistently."""
    print()
    print(paint("=" * WIDTH, "red"))
    header = f" ERROR{f' [{code}]' if code else ''}"
    print(paint(header, "bold", "red"))
    print(paint("=" * WIDTH, "red"))
    for line in str(exc).splitlines():
        print(f"  {line}")
    if remedy:
        print()
        print(paint("  How to fix:", "bold"))
        for line in remedy.splitlines():
            print(f"    {line}")
    print()
