import asyncio
from dataclasses import dataclass

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from castcode.ui.input import InlineInput
from castcode.ui.messages import ChatMessage


@dataclass
class PromptResult:
    action: str
    answers: dict | None = None


@dataclass(frozen=True)
class Choice:
    action: str
    label: str
    description: str = ""


class InlineChoice(Vertical):
    class Hovered(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    class Left(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self.index = 0
        self._input = InlineInput(classes="inline-input", compact=True)
        self._input.display = False

    def compose(self) -> ComposeResult:
        yield Static("", markup=False, classes="inline-choice-label")
        yield Static("", markup=False, classes="inline-choice-description")
        yield self._input

    def update_choice(self, label: str, description: str = "",
                      active: bool = False, freeform: bool = False) -> None:
        self.display = True
        self.set_class(active, "-active")
        self.set_class(freeform, "-freeform")
        self.query_one(".inline-choice-label", Static).display = True
        self.query_one(".inline-choice-label", Static).update(label)
        desc = self.query_one(".inline-choice-description", Static)
        desc.update(description)
        desc.display = bool(description)
        self._input.display = False

    def show_input(self, placeholder: str, text: str = "") -> InlineInput:
        self.query_one(".inline-choice-description", Static).display = False
        self._input.placeholder = placeholder
        if self._input.value != text:
            self._input.clear()
            if text:
                self._input.value = text
        self._input.display = True
        return self._input

    def hide_input(self) -> None:
        self._input.display = False

    @property
    def input_text(self) -> str:
        return self._input.value

    async def _on_enter(self, event: events.Enter) -> None:
        self.post_message(self.Hovered(self.index))

    async def _on_leave(self, event: events.Leave) -> None:
        self.post_message(self.Left(self.index))


_HELP_SELECT = "Enter to select · ↑/↓ to navigate · Esc to cancel"
_HELP_MULTI = "Enter to confirm · Space to toggle · ↑/↓ to navigate · Esc to cancel"
_HELP_TYPE = "Enter to send · Esc to cancel"


class ResolvablePrompt(ChatMessage):
    marker = "☐"
    can_focus = True
    transient = True

    def __init__(self) -> None:
        super().__init__(text="")
        self._done = asyncio.Event()
        self._result = None

    def on_mount(self) -> None:
        self._refresh()

    async def wait(self) -> PromptResult:
        await self._done.wait()
        return self._result

    def cancel(self) -> None:
        self._resolve(PromptResult("cancel"))

    def _resolve(self, result: PromptResult) -> None:
        if self._done.is_set():
            return
        self._result = result
        self._done.set()


class ToolApprovalPrompt(ResolvablePrompt):
    def __init__(self, title: str, description: str, choices: list[Choice]) -> None:
        super().__init__()
        self.title = title
        self.description = description
        self.choices = choices
        self.rows = [InlineChoice() for _ in choices]
        self.index = 0

    def _make_body(self):
        return Vertical(
            Static(self.title, markup=False, classes="inline-title"),
            Static(self.description, markup=False, classes="inline-description"),
            Vertical(*self.rows, classes="inline-choices"),
            Static(_HELP_SELECT, markup=False, classes="inline-help"),
            classes="body",
        )

    def _refresh(self) -> None:
        for i, row in enumerate(self.rows):
            cursor = "❯" if i == self.index else " "
            choice = self.choices[i]
            row.update_choice(
                f"{cursor} {i + 1}. {choice.label}",
                choice.description,
                active=i == self.index,
            )

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.cancel()
        elif event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            self.index = (self.index + (1 if event.key == "down" else -1)) % len(self.choices)
            self._refresh()
        elif event.key in ("enter", "space"):
            event.stop()
            event.prevent_default()
            self._resolve(PromptResult(self.choices[self.index].action))
        elif event.key.isdigit() and 1 <= int(event.key) <= len(self.choices):
            event.stop()
            event.prevent_default()
            self.index = int(event.key) - 1
            self._resolve(PromptResult(self.choices[self.index].action))


class QuestionPrompt(ResolvablePrompt):
    def __init__(self, questions: list[dict]) -> None:
        super().__init__()
        self.questions = questions
        self.question_index = 0
        self.index = 0
        self.indices = {i: 0 for i in range(len(questions))}
        self.answers = {}
        self.answered = set()
        self.selected = {
            i: set() for i, q in enumerate(questions) if q.get("multiSelect")
        }
        self.freeform = {i: "" for i in range(len(questions))}
        count = max(len(q.get("options", [])) + 1 for q in questions)
        self.rows = [InlineChoice() for _ in range(count)]
        self._typing = False
        self._reviewing = False
        self._ignore_hover_index = None

    def _make_body(self):
        return Vertical(
            Static("", markup=False, classes="inline-title"),
            Static("", markup=False, classes="inline-description"),
            Vertical(*self.rows, classes="inline-choices"),
            Static("", markup=False, classes="inline-help"),
            classes="body",
        )

    def _current(self) -> dict:
        return self.questions[self.question_index]

    def _question_key(self) -> str:
        return self._current().get("question", "")

    def _options(self) -> list[dict]:
        return self._current().get("options", [])

    def _is_multi(self) -> bool:
        return bool(self._current().get("multiSelect"))

    def _count(self) -> int:
        return len(self._options()) + 1

    def _freeform_index(self) -> int:
        return len(self._options())

    def _capture_freeform(self) -> None:
        index = self._freeform_index()
        if index < len(self.rows) and self.rows[index]._input.display:
            self.freeform[self.question_index] = self.rows[index].input_text

    def _is_reviewing(self) -> bool:
        return self._reviewing and self.question_index in self.answered

    def _go_to_question(self, question_index: int, *, review_answer: bool = True) -> None:
        self._capture_freeform()
        self.indices[self.question_index] = self.index
        self.question_index = question_index % len(self.questions)
        self.index = min(self.indices.get(self.question_index, 0), self._count() - 1)
        self._typing = False
        self._reviewing = review_answer and self.question_index in self.answered
        self._ignore_hover_index = None
        self._refresh()
        if not self._typing:
            self.focus()

    def _next_unanswered(self) -> int:
        for offset in range(1, len(self.questions) + 1):
            index = (self.question_index + offset) % len(self.questions)
            if index not in self.answered:
                return index
        return self.question_index

    def _refresh(self) -> None:
        if self._typing:
            self._capture_freeform()
        current = self._current()
        self.index = min(self.index, self._count() - 1)
        self.indices[self.question_index] = self.index
        reviewing = self._is_reviewing()
        title = current.get("header") or "Question"
        self.query_one(".inline-title", Static).update(
            f"{title}  {self.question_index + 1}/{len(self.questions)}"
        )
        self.query_one(".inline-description", Static).update(current.get("question", ""))
        selected = self.selected.get(self.question_index, set())
        self._typing = self.index == self._freeform_index() and not reviewing
        for i, row in enumerate(self.rows):
            row.index = i
            if i >= self._count():
                row.hide_input()
                row.display = False
                continue
            cursor = "❯" if i == self.index else " "
            if i < len(self._options()):
                option = self._options()[i]
                mark = ""
                if self._is_multi():
                    mark = "[x] " if i in selected else "[ ] "
                row.update_choice(
                    f"{cursor} {i + 1}. {mark}{option.get('label', '')}",
                    str(option.get("description") or ""),
                    active=i == self.index,
                )
            else:
                description = self.freeform.get(self.question_index, "") or "Type something."
                if reviewing:
                    description = str(
                        self.answers.get(
                            self._question_key(),
                            self.freeform.get(self.question_index, ""),
                        )
                        or description
                    )
                row.update_choice(
                    f"{cursor} {i + 1}. Answer in your own words.",
                    description,
                    active=i == self.index,
                    freeform=True,
                )
                if i == self.index and not reviewing:
                    field = row.show_input(
                        "Type something.",
                        self.freeform.get(self.question_index, ""),
                    )
                    field.focus()
        help_text = (
            _HELP_TYPE
            if self._typing
            else (_HELP_MULTI if self._is_multi() else _HELP_SELECT)
        )
        self.query_one(".inline-help", Static).update(help_text)

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.cancel()
            return
        if self._typing:
            return
        if event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            self._reviewing = False
            previous = self.index
            self.index = (self.index + (1 if event.key == "down" else -1)) % self._count()
            self._ignore_hover_index = previous
            self._refresh()
        elif event.key in ("left", "right") and len(self.questions) > 1:
            event.stop()
            event.prevent_default()
            self._go_to_question(
                self.question_index + (1 if event.key == "right" else -1)
            )
        elif event.key == "space":
            event.stop()
            event.prevent_default()
            self._reviewing = False
            if self._is_multi() and self.index < len(self._options()):
                self._toggle()
        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            if self._is_reviewing() and self.index == self._freeform_index():
                self._reviewing = False
                self._refresh()
                return
            self._select()
        elif event.key.isdigit() and 1 <= int(event.key) <= self._count():
            event.stop()
            event.prevent_default()
            self._reviewing = False
            self.index = int(event.key) - 1
            if self._is_multi() and self.index < len(self._options()):
                self._toggle()
            elif self.index < len(self._options()):
                self._select()
            else:
                self._refresh()

    def on_inline_choice_hovered(self, event: InlineChoice.Hovered) -> None:
        event.stop()
        if self._is_reviewing():
            return
        if event.index >= self._count():
            return
        if event.index == self._ignore_hover_index:
            return
        self._ignore_hover_index = None
        if self._typing:
            self._capture_freeform()
            return
        self.index = event.index
        self._refresh()

    def on_inline_choice_left(self, event: InlineChoice.Left) -> None:
        event.stop()
        if event.index == self._ignore_hover_index:
            self._ignore_hover_index = None

    def _toggle(self) -> None:
        selected = self.selected[self.question_index]
        if self.index in selected:
            selected.remove(self.index)
        else:
            selected.add(self.index)
        self._refresh()

    def _select(self) -> None:
        options = self._options()
        if self.index == len(options):
            self._refresh()
            return
        if self._is_multi():
            chosen = sorted(self.selected[self.question_index]) or [self.index]
            self._finish([options[i].get("label", "") for i in chosen])
        else:
            self._finish(options[self.index].get("label", ""))

    def _finish(self, answer) -> None:
        self._capture_freeform()
        self.answers[self._question_key()] = answer
        self.answered.add(self.question_index)
        self._reviewing = False
        if len(self.answered) == len(self.questions):
            self._resolve(PromptResult("questions", answers=self.answers))
            return
        self._go_to_question(self._next_unanswered(), review_answer=False)

    def on_inline_input_navigate(self, event: InlineInput.Navigate) -> None:
        event.stop()
        previous = self.index
        self._capture_freeform()
        self.index = (self.index + (1 if event.direction == "down" else -1)) % self._count()
        self._typing = False
        self._ignore_hover_index = previous
        self._refresh()
        if not self._typing:
            self.focus()

    def on_inline_input_navigate_question(self, event: InlineInput.NavigateQuestion) -> None:
        event.stop()
        self._capture_freeform()
        self._typing = False
        self._go_to_question(
            self.question_index + (1 if event.direction == "right" else -1)
        )

    def on_inline_input_submitted(self, event: InlineInput.Submitted) -> None:
        if not self._typing:
            return
        event.stop()
        self._typing = False
        self.focus()
        self._finish(event.text)
        if not self._done.is_set() and not self._typing:
            self.focus()
