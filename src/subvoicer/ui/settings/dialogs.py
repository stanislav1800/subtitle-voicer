"""Dialogs of the «Профили и голоса» tab: character, merge, import, dictionary entry, stop-list."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from subvoicer.config.profile import Character, PronunciationRule
from subvoicer.core.models import Gender, Voice
from subvoicer.ui.widgets.common import combo, label, primary

GENDERS = ((Gender.MALE, "муж."), (Gender.FEMALE, "жен."), (Gender.UNKNOWN, "не знаю"))


def gender_text(gender: Gender) -> str:
    return "—" if gender is Gender.UNKNOWN else dict(GENDERS)[gender]


def voice_text(voice_id: str | None, voices: Sequence[Voice]) -> str:
    if not voice_id:
        return "—"
    for voice in voices:
        if voice.id == voice_id:
            short = {Gender.MALE: " · муж.", Gender.FEMALE: " · жен."}.get(voice.gender, "")
            return f"{voice.display_name}{short}"
    return voice_id


def voice_items(voices: Sequence[Voice], empty: str | None = None) -> list[tuple[str, object]]:
    items: list[tuple[str, object]] = [(empty, "")] if empty is not None else []
    items += [(voice_text(v.id, voices), v.id) for v in voices]
    return items


class Form(QDialog):
    """Title, a form, OK/Cancel; ``ok_text`` names the action."""

    def __init__(self, title: str, parent: QWidget | None, ok_text: str = "Сохранить") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(24, 20, 24, 20)
        self.layout_.setSpacing(12)
        self.layout_.addWidget(label(title, "windowTitle"))
        self.form = QFormLayout()
        self.form.setSpacing(10)
        self.layout_.addLayout(self.form)
        buttons = QDialogButtonBox()
        self.ok = primary(ok_text)
        buttons.addButton(self.ok, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.buttons = buttons
        self.layout_.addWidget(buttons)


class CharacterDialog(Form):
    """Add or change a character; a changed gender or voice is locked (TZ 4.6)."""

    def __init__(
        self,
        parent: QWidget | None,
        voices: Sequence[Voice],
        listen: Callable[[str, str], None],
        character: Character | None = None,
    ) -> None:
        super().__init__("Изменить персонажа" if character else "Новый персонаж", parent)
        self._original = character
        self._name = QLineEdit(character.name if character else "")
        self._aliases = QLineEdit(", ".join(character.aliases) if character else "")
        self._aliases.setPlaceholderText("через запятую")
        self._gender = combo(
            [(t, g.value) for g, t in GENDERS],
            (character.gender if character else Gender.UNKNOWN).value,
            "Пол",
        )
        current = character.voice if character else None
        self._voice = combo(
            voice_items(voices, "назначить при первой реплике"), current or "", "Голос"
        )
        listen_button = QPushButton("Прослушать")
        listen_button.clicked.connect(
            lambda: listen(self._voice.currentData() or "", self._name.text().strip())
        )
        voice_row = QHBoxLayout()
        voice_row.addWidget(self._voice, 1)
        voice_row.addWidget(listen_button)
        self.form.addRow("Имя", self._name)
        self.form.addRow("Варианты имени", self._aliases)
        self.form.addRow("Пол", self._gender)
        self.form.addRow("Голос", voice_row)

    def result_character(self) -> Character | None:
        name = self._name.text().strip()
        if not name:
            return None
        aliases = [a.strip() for a in self._aliases.text().split(",") if a.strip()]
        gender = Gender(self._gender.currentData())
        voice = self._voice.currentData() or None
        old = self._original
        if old is None:
            return Character(
                name=name,
                aliases=aliases,
                gender=gender,
                gender_locked=gender is not Gender.UNKNOWN,
                voice=voice,
                voice_locked=voice is not None,
                source="manual",
            )
        return old.model_copy(
            update={
                "name": name,
                "aliases": aliases,
                "gender": gender,
                "gender_locked": old.gender_locked or gender is not old.gender,
                "voice": voice,
                "voice_locked": old.voice_locked or voice != old.voice,
            }
        )


class MergeDialog(Form):
    def __init__(self, parent: QWidget | None, source: str, names: Sequence[str]) -> None:
        super().__init__("Объединить персонажей", parent, ok_text="Объединить")
        self.layout_.insertWidget(
            1,
            label(
                f"«{source}» станет вариантом имени выбранного персонажа. Голос и пол "
                "останутся от него.",
                "caption",
                wrap=True,
            ),
        )
        self._target = combo([(n, n) for n in names if n != source], "", "С кем объединить")
        self.form.addRow("С кем", self._target)

    def target(self) -> str:
        return str(self._target.currentData() or "")


class ImportDialog(Form):
    """Characters from a file or pasted text: ``имя;пол;варианты`` (TZ 4.6)."""

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__("Импорт персонажей", parent, ok_text="Импортировать")
        self.layout_.insertWidget(
            1,
            label(
                "По строке на персонажа: имя;пол;варианты. Пол — м, ж или пусто, варианты "
                "через |. Строка заголовка не обязательна.",
                "caption",
                wrap=True,
            ),
        )
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "Джуди Альварес;ж;Джуди\nДжонни Сильверхенд;м;Джонни|Сильверхенд"
        )
        self.text.setMinimumHeight(200)
        self.form.addRow(self.text)
        from_file = QPushButton("Из файла…")
        from_file.clicked.connect(self._load)
        self.form.addRow(from_file)

    def _load(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Файл персонажей", "", "Текст (*.csv *.txt)")
        if name:
            self.text.setPlainText(Path(name).read_text(encoding="utf-8-sig"))


class EntryDialog(Form):
    """A dictionary entry with «Прослушать» before saving (TZ 4.7.1, 4.13.1)."""

    def __init__(
        self,
        parent: QWidget | None,
        listen: Callable[[PronunciationRule, str], None],
        entry: PronunciationRule | None = None,
        sample: str = "",
    ) -> None:
        super().__init__("Запись словаря", parent)
        self._source = QLineEdit(entry.from_ if entry else "")
        self._source.setObjectName("mono")
        self._target = QLineEdit(entry.to if entry else "")
        self._target.setObjectName("mono")
        self._regex = QCheckBox("Регулярное выражение")
        self._regex.setChecked(entry.regex if entry else False)
        self._sample = QLineEdit(sample)
        self._sample.setPlaceholderText("фраза для прослушивания (по умолчанию — само слово)")
        self.form.addRow("Как написано", self._source)
        self.form.addRow("Как читать", self._target)
        self.form.addRow("", self._regex)
        self.form.addRow("Фраза", self._sample)
        self.layout_.insertWidget(
            2,
            label("«+» перед гласной — ударение; «*» в конце — все формы слова.", "hint"),
        )
        listen_button = QPushButton("Прослушать")
        listen_button.clicked.connect(self._listen)
        self.buttons.addButton(listen_button, QDialogButtonBox.ButtonRole.ActionRole)
        self._listen_cb = listen

    def entry(self) -> PronunciationRule | None:
        source, target = self._source.text().strip(), self._target.text().strip()
        if not source or not target:
            return None
        return PronunciationRule.model_validate(
            {"from": source, "to": target, "regex": self._regex.isChecked()}
        )

    def _listen(self) -> None:
        entry = self.entry()
        if entry is not None:
            phrase = self._sample.text().strip() or entry.from_.rstrip("*")
            self._listen_cb(entry, phrase)


class StoplistDialog(Form):
    def __init__(self, parent: QWidget | None, words: Sequence[str]) -> None:
        super().__init__("Не считать именами", parent)
        self.layout_.insertWidget(
            1,
            label(
                "Слова перед двоеточием, которые не имя говорящего («Совет: …»). "
                "По одному в строке.",
                "caption",
                wrap=True,
            ),
        )
        self.text = QPlainTextEdit("\n".join(words))
        self.text.setMinimumHeight(220)
        self.form.addRow(self.text)

    def words(self) -> list[str]:
        return [w.strip() for w in self.text.toPlainText().splitlines() if w.strip()]
