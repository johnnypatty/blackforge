from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TypeAlias

from .models import Tool
from .sources import ArchTool


class TuiError(RuntimeError):
    pass


TuiTool: TypeAlias = Tool | ArchTool


def _category(tool: TuiTool) -> str:
    if isinstance(tool, ArchTool):
        return ", ".join(tool.categories)
    return tool.category


def _reference(tool: TuiTool) -> str:
    if isinstance(tool, ArchTool):
        return tool.id
    return tool.name


@dataclass(slots=True)
class TuiState:
    tools: list[TuiTool]
    query: str = ""
    cursor: int = 0
    selected: set[str] = field(default_factory=set)

    @property
    def visible(self) -> list[TuiTool]:
        terms = self.query.casefold().split()
        if not terms:
            return self.tools
        return [
            tool
            for tool in self.tools
            if all(
                term in f"{tool.name} {tool.description} {_category(tool)}".casefold()
                for term in terms
            )
        ]

    def move(self, amount: int) -> None:
        visible = self.visible
        if not visible:
            self.cursor = 0
            return
        self.cursor = min(max(0, self.cursor + amount), len(visible) - 1)

    def set_query(self, query: str) -> None:
        self.query = query.strip()
        self.cursor = 0

    def toggle(self) -> None:
        visible = self.visible
        if not visible:
            return
        reference = _reference(visible[self.cursor])
        if reference in self.selected:
            self.selected.remove(reference)
        else:
            self.selected.add(reference)


def run_tui(tools: Iterable[TuiTool]) -> list[str]:
    try:
        import curses
    except ImportError as exc:
        raise TuiError(
            "The full-screen interface requires Python curses on Linux"
        ) from exc
    state = TuiState(list(tools))

    def application(screen) -> list[str] | None:
        curses.curs_set(0)
        screen.keypad(True)
        while True:
            _draw(screen, state)
            key = screen.getch()
            if key in {ord("q"), 27}:
                return None
            if key in {curses.KEY_UP, ord("k")}:
                state.move(-1)
            elif key in {curses.KEY_DOWN, ord("j")}:
                state.move(1)
            elif key == curses.KEY_PPAGE:
                state.move(-10)
            elif key == curses.KEY_NPAGE:
                state.move(10)
            elif key == ord(" "):
                state.toggle()
            elif key == ord("/"):
                state.set_query(_prompt(screen, "Search: "))
            elif key in {10, 13, curses.KEY_ENTER}:
                if state.selected:
                    return sorted(state.selected)
                visible = state.visible
                return [visible[state.cursor].name] if visible else []
            elif key == ord("i"):
                visible = state.visible
                if visible:
                    _details(screen, visible[state.cursor])
        return None

    try:
        result = curses.wrapper(application)
    except curses.error as exc:
        raise TuiError(
            "Unable to open the full-screen interface; use `blackforge interactive` instead"
        ) from exc
    return result or []


def _draw(screen, state: TuiState) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    title = (
        f" BlackForge | {len(state.visible)} tools | "
        f"{len(state.selected)} selected | / search  space select  i info  enter continue  q quit "
    )
    screen.addnstr(0, 0, title, max(1, width - 1))
    if state.query:
        screen.addnstr(1, 0, f"Filter: {state.query}", max(1, width - 1))
    visible = state.visible
    page_height = max(1, height - 3)
    start = max(0, min(state.cursor - page_height + 1, len(visible) - page_height))
    for row, tool in enumerate(visible[start : start + page_height], start=2):
        index = start + row - 2
        marker = "[x]" if _reference(tool) in state.selected else "[ ]"
        line = f"{marker} {tool.name:<28} {_category(tool):<24} {tool.description}"
        attributes = 0
        if index == state.cursor:
            attributes = __import__("curses").A_REVERSE
        screen.addnstr(row, 0, line, max(1, width - 1), attributes)
    screen.refresh()


def _prompt(screen, label: str) -> str:
    import curses

    height, width = screen.getmaxyx()
    curses.echo()
    curses.curs_set(1)
    try:
        screen.move(height - 1, 0)
        screen.clrtoeol()
        screen.addnstr(height - 1, 0, label, max(1, width - 1))
        value = screen.getstr(height - 1, len(label), max(1, width - len(label) - 1))
        return value.decode("utf-8", errors="replace")
    finally:
        curses.noecho()
        curses.curs_set(0)


def _details(screen, tool: TuiTool) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    lines = [
        tool.name,
        f"Version: {tool.version or '-'}",
        f"Source: {'Official Arch' if isinstance(tool, ArchTool) else 'BlackArch'}",
        f"Category: {_category(tool)}",
        f"Website: {tool.website or '-'}",
        "",
        tool.description,
        "",
        "Press any key to return.",
    ]
    row = 0
    for value in lines:
        for wrapped in _wrap(value, max(10, width - 1)):
            if row >= height - 1:
                break
            screen.addnstr(row, 0, wrapped, max(1, width - 1))
            row += 1
    screen.refresh()
    screen.getch()


def _wrap(value: str, width: int) -> list[str]:
    if not value:
        return [""]
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
