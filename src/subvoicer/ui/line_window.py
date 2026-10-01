"""Line window and pronunciation fix (TZ 4.13.1, docs/design/LineFix.html)."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from subvoicer.app.facade import AppFacade
from subvoicer.config.profile import Profile, PronunciationRule
from subvoicer.config.profile_store import format_error
from subvoicer.core.models import HistoryEntry
from subvoicer.ui import theme
from subvoicer.ui.settings.dialogs import voice_text
from subvoicer.ui.widgets.common import chip, label, primary, toggle_row
from subvoicer.ui.widgets.flow import FlowLayout

WORD = re.compile(r"[\w][\w\-]*", re.UNICODE)
ENDING = set("аеёиоуыэюяьй")
ALL_FORMS_MIN_LETTERS = 5  # TZ: on by default for words longer than 4 letters


def words(text: str) -> list[str]:
    return [w for w in WORD.findall(text) if not w.isdigit()]


def stem(word: str) -> str:
    """The word without 1-2 ending letters (vowels, ь, й): «Арасака» -> «Арасак»,
    «Милитех» -> «Милитех» (its forms are «Милитеха», «Милитехом»)."""
    result = word
    for _ in range(2):
        if len(result) > 3 and result[-1].lower() in ENDING:
            result = result[:-1]
    return result


def suggested_entry(word: str, all_forms: bool) -> tuple[str, str]:
    """``from`` and the initial ``to`` for a clicked word."""
    if all_forms:
        base = stem(word)
        return f"{base}*", f"{base}*"
    return word, word


class LineWindow(QDialog):
    def __init__(self, facade: AppFacade, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._facade = facade
        self._entry: HistoryEntry | None = None
        self._word = ""
        self.setWindowTitle("Реплика")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        head = QHBoxLayout()
        self._speaker = chip("", "warning")
        head.addWidget(self._speaker)
        self._meta = label("", "hint")
        head.addWidget(self._meta, 1)
        layout.addLayout(head)
        self._text = label("", "", wrap=True)
        self._text.setStyleSheet("font-size: 17px;")
        layout.addWidget(self._text)
        buttons = QHBoxLayout()
        repeat = QPushButton("  Повторить")
        repeat.setIcon(theme.icon("play", theme.ACCENT))
        repeat.clicked.connect(self._repeat)
        buttons.addWidget(repeat)
        self._fix_button = QPushButton("Исправить произношение")
        self._fix_button.setCheckable(True)
        self._fix_button.toggled.connect(self._toggle_fix)
        buttons.addWidget(self._fix_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._fix = QFrame()
        self._fix.setObjectName("card")
        fix = QVBoxLayout(self._fix)
        fix.setContentsMargins(18, 16, 18, 16)
        fix.setSpacing(12)
        fix.addWidget(label("Нажмите на слово, которое звучит неправильно", "caption"))
        self._words_box = QWidget()
        self._words = FlowLayout(self._words_box)
        fix.addWidget(self._words_box)
        forms_box, self._all_forms = toggle_row("Все формы слова", "", True)
        self._all_forms.toggled.connect(self._on_forms)
        fix.addWidget(forms_box)
        fields = QHBoxLayout()
        source_box, target_box = QVBoxLayout(), QVBoxLayout()
        source_box.addWidget(label("Как написано", "caption"))
        self._source = QLineEdit()
        self._source.setObjectName("mono")
        source_box.addWidget(self._source)
        target_box.addWidget(label("Как читать", "caption"))
        self._target = QLineEdit()
        self._target.setObjectName("mono")
        target_box.addWidget(self._target)
        fields.addLayout(source_box, 1)
        fields.addLayout(target_box, 1)
        fix.addLayout(fields)
        fix.addWidget(
            label("«+» ставится перед ударной гласной. «*» — любое окончание слова.", "hint")
        )
        self._message = label("", "hint", wrap=True)
        fix.addWidget(self._message)
        actions = QHBoxLayout()
        actions.addStretch(1)
        listen = QPushButton("  Прослушать")
        listen.setIcon(theme.icon("play", theme.ACCENT))
        listen.clicked.connect(self._listen)
        actions.addWidget(listen)
        self._save = primary("Сохранить", self._save_entry)
        actions.addWidget(self._save)
        fix.addLayout(actions)
        layout.addWidget(self._fix)
        self._fix.hide()

    # ------------------------------------------------------------------ showing

    def show_entry(self, entry: HistoryEntry) -> None:
        self._entry = entry
        self._speaker.setText(entry.speaker or "Рассказчик")
        voice = (
            voice_text(entry.voice, self._facade.voices()) if entry.voice else "голос не назначен"
        )
        self._meta.setText(f"{voice} · {entry.time:%H:%M:%S}")
        self._text.setText(entry.text)
        while self._words.count():
            item = self._words.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().setParent(None)
        for word in words(entry.text):
            button = QPushButton(word)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, w=word, b=button: self._pick(w, b))
            self._words.addWidget(button)
        self._word = ""
        self._source.clear()
        self._target.clear()
        self._message.setText("")
        profile_title = self._profile_title()
        self._save.setText(f"Сохранить в {profile_title}" if profile_title else "Сохранить")
        self._save.setEnabled(profile_title is not None)
        self.show()
        self.raise_()
        self.activateWindow()

    def _toggle_fix(self, on: bool) -> None:
        self._fix.setVisible(on)
        self.adjustSize()

    def _pick(self, word: str, button: QPushButton) -> None:
        for index in range(self._words.count()):
            item = self._words.itemAt(index)
            other = item.widget() if item is not None else None
            if isinstance(other, QPushButton) and other is not button:
                other.setChecked(False)
        button.setChecked(True)
        self._word = word
        self._all_forms.blockSignals(True)
        self._all_forms.setChecked(len(word) >= ALL_FORMS_MIN_LETTERS)
        self._all_forms.blockSignals(False)
        self._on_forms(self._all_forms.isChecked())

    def _on_forms(self, all_forms: bool) -> None:
        if not self._word:
            return
        source, target = suggested_entry(self._word, all_forms)
        self._source.setText(source)
        self._target.setText(target)
        self._target.setFocus()

    # ------------------------------------------------------------------ actions

    def _rule(self) -> PronunciationRule | None:
        source, target = self._source.text().strip(), self._target.text().strip()
        if not source or not target:
            return None
        return PronunciationRule.model_validate({"from": source, "to": target})

    def _voice(self) -> str:
        if self._entry is not None and self._entry.voice:
            return self._entry.voice
        voices = self._facade.voices()
        return voices[0].id if voices else ""

    def _repeat(self) -> None:
        if self._entry is not None:
            self._facade.say(self._entry.text, self._voice(), self._entry.speaker)

    def _listen(self) -> None:
        rule = self._rule()
        if self._entry is None or rule is None:
            return
        self._facade.say(
            self._entry.text,
            self._voice(),
            self._entry.speaker,
            pronunciation=((rule.from_, rule.to, False),),
        )

    def _profile_path(self) -> Path | None:
        path = self._facade.status().profile_path
        return Path(path) if path else None

    def _profile_title(self) -> str | None:
        path = self._profile_path()
        if path is None:
            return None
        try:
            return self._facade.profile(path).title or path.stem
        except (OSError, ValueError):
            return None

    def _save_entry(self) -> None:
        rule, path = self._rule(), self._profile_path()
        if rule is None or path is None:
            return

        def change(profile: Profile) -> Profile:
            key = rule.from_.strip().lower()
            rules = [r for r in profile.pronunciation if r.from_.strip().lower() != key]
            return profile.model_copy(update={"pronunciation": [*rules, rule]})

        try:
            self._facade.update_profile(path, change)
        except (OSError, ValueError) as exc:
            self._message.setText(f"Не сохранено: {format_error(exc)}")
            return
        self._message.setText(
            f"Сохранено: {rule.from_} → {rule.to}. Действует со следующей реплики."
        )
