"""«Профили и голоса» (TZ 4.13, docs/design/Profiles.html): games, zones, voices, characters,
pronunciation and the speaker stop-list of the selected profile."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from subvoicer.app.facade import AppFacade
from subvoicer.config.profile import Character, Profile, PronunciationRule, Zone
from subvoicer.config.profile_store import format_error
from subvoicer.core.models import Gender
from subvoicer.ui import theme
from subvoicer.ui.settings.dialogs import (
    CharacterDialog,
    EntryDialog,
    ImportDialog,
    MergeDialog,
    StoplistDialog,
    gender_text,
    voice_items,
    voice_text,
)
from subvoicer.ui.settings.window import Page
from subvoicer.ui.widgets.common import (
    Card,
    ElidedLabel,
    chip,
    clear_layout,
    combo,
    icon_button,
    label,
    primary,
)

log = logging.getLogger(__name__)

GameEditor = Callable[[Path, str], None]  # (profile path, "game" | "screenshot")
PRIORITY_TEXT = {"high": "высокий", "normal": "обычный", "low": "низкий"}
TRIGGER_TEXT = {"auto": "Авто", "hotkey": "По клавише", "off": "Выключена"}
VOICE_MODE_TEXT = {"speaker": "По говорящему", "narrator": "Рассказчик", "fixed": "Свой голос"}


def initials(title: str) -> str:
    words = [w for w in title.replace(".exe", "").split() if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return title[:2].upper() if title else "??"


def profile_status(profile: Profile, missing_aspect: str | None = None) -> str:
    if missing_aspect:
        return f"Нет зон для {missing_aspect}"
    if not any(layout.zones for layout in profile.layouts.values()):
        return "Не настроено"
    return "Настроено · " + ", ".join(k for k, v in profile.layouts.items() if v.zones)


def zone_behaviour(zone: Zone) -> str:
    if zone.kind == "speaker":
        return "Имя говорящего"
    policy = zone.policy
    if zone.trigger == "hotkey":
        return f"По клавише {zone.hotkey or ''}".strip()
    parts = [TRIGGER_TEXT.get(zone.trigger or "", "")]
    if zone.trigger == "auto" and policy is not None:
        parts.append(f"{PRIORITY_TEXT[policy.priority]} приоритет")
        if policy.repeat_cooldown_min:
            parts.append(f"не повторять {policy.repeat_cooldown_min:g} мин")
        if not policy.duck:
            parts.append("без приглушения")
    return " · ".join(p for p in parts if p)


def assigned_chip(character: Character) -> QLabel:
    if character.gender is Gender.UNKNOWN and not character.gender_locked and character.voice:
        return chip("уточнить пол", "warning")
    if character.voice_locked or character.source == "manual":
        return chip("вручную", "success")
    if character.source == "import":
        return chip("из списка", "import")
    return chip("авто", "info")


def badge(text: str, size: int = 40) -> QLabel:
    result = QLabel(text)
    result.setFixedSize(size, size)
    result.setAlignment(Qt.AlignmentFlag.AlignCenter)
    result.setStyleSheet(
        f"background: {theme.BG_CONTROL}; color: {theme.ACCENT}; border-radius: 8px;"
        f" font-family: '{theme.family(theme.HEADING_FAMILY)}'; font-weight: 700;"
    )
    return result


class ProfilesPage(Page):
    def __init__(self, facade: AppFacade, open_editor: GameEditor) -> None:
        super().__init__("Профили и голоса")
        self._facade = facade
        self._open_editor = open_editor
        self._selected: Path | None = None
        self._aspect: str | None = None
        columns = QHBoxLayout()
        columns.setSpacing(16)
        self.content.addLayout(columns)

        left = QVBoxLayout()
        left.setSpacing(16)
        games = Card("Игры")
        self._games = QListWidget()
        self._games.setMinimumHeight(220)
        self._games.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._games.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._games.currentItemChanged.connect(self._on_game)
        games.body.addWidget(self._games)
        add = QPushButton("  Добавить игру")
        add.setToolTip("Выбрать exe игры")
        add.setIcon(theme.icon("plus"))
        add.clicked.connect(self._add_game)
        games.body.addWidget(add)
        imported = QPushButton("  Импорт профиля")
        imported.setIcon(theme.icon("download"))
        imported.clicked.connect(self._import_profile)
        games.body.addWidget(imported)
        left.addWidget(games)
        self._dictionary = Card("Произношение")
        left.addWidget(self._dictionary)
        left.addStretch(1)
        left_box = QWidget()
        left_box.setLayout(left)
        left_box.setFixedWidth(230)
        columns.addWidget(left_box)

        columns.setContentsMargins(0, 0, 12, 0)  # room for the vertical scroll bar
        self._detail = QVBoxLayout()
        self._detail.setSpacing(16)
        detail_box = QWidget()
        detail_box.setLayout(self._detail)
        columns.addWidget(detail_box, 1)
        self.finish()

    # ------------------------------------------------------------------ games

    def refresh(self) -> None:
        selected = self._selected
        self._games.blockSignals(True)
        self._games.clear()
        status = self._facade.status()
        stored_list = self._facade.profiles()
        for stored in stored_list:
            missing = None
            if status.profile_path == str(stored.path) and not status.has_zones:
                missing = status.aspect
            item = QListWidgetItem(
                f"{stored.profile.title or stored.path.stem}\n"
                f"{profile_status(stored.profile, missing)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, str(stored.path))
            item.setIcon(theme.icon("gamepad-2", theme.ACCENT, 20))
            self._games.addItem(item)
            if selected is not None and stored.path == selected:
                self._games.setCurrentItem(item)
        self._games.blockSignals(False)
        if self._games.currentItem() is None and self._games.count():
            self._games.setCurrentRow(0)
        self._on_game(self._games.currentItem(), None)

    def _on_game(self, item: QListWidgetItem | None, _previous: object) -> None:
        if item is None:
            self._selected = None
            clear_layout(self._detail)
            self._detail.addWidget(
                label(
                    "Профилей пока нет. Добавьте игру по exe или запустите игру — "
                    "приложение предложит создать профиль.",
                    "caption",
                    wrap=True,
                )
            )
            clear_layout(self._dictionary.body)
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if path != self._selected:
            self._aspect = None
        self._selected = path
        self._show_profile(path)

    def _add_game(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Исполняемый файл игры", "", "Программы (*.exe)"
        )
        if not name:
            return
        try:
            stored = self._facade.add_game(name)
        except (OSError, ValueError, FileExistsError) as exc:
            QMessageBox.warning(self, "Профиль не создан", format_error(exc))
            return
        self._selected = stored.path
        self.refresh()

    def _import_profile(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Профиль", "", "Профиль (*.json)")
        if not name:
            return
        try:
            stored = self._facade.import_profile(Path(name))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Профиль не импортирован", format_error(exc))
            return
        self._selected = stored.path
        self.refresh()

    # ------------------------------------------------------------------ one profile

    def _show_profile(self, path: Path) -> None:
        try:
            profile = self._facade.profile(path)
        except (OSError, ValueError) as exc:
            clear_layout(self._detail)
            self._detail.addWidget(
                label(f"Профиль не читается: {format_error(exc)}", "caption", wrap=True)
            )
            return
        clear_layout(self._detail)
        self._detail.addWidget(self._header(path, profile))
        self._detail.addWidget(self._zones(path, profile))
        self._detail.addWidget(self._voices(path, profile))
        self._detail.addWidget(self._characters(path, profile))
        self._detail.addWidget(self._stoplist(path, profile))
        self._fill_dictionary(path, profile)
        self.fit_height()

    def _header(self, path: Path, profile: Profile) -> QWidget:
        card = Card()
        top = QHBoxLayout()
        title = profile.title or path.stem
        top.addWidget(badge(initials(title)))
        names = QVBoxLayout()
        names.addWidget(ElidedLabel(title, "windowTitle"))
        where = " · ".join(p for p in (profile.exe or "без exe", profile.install_dir) if p)
        names.addWidget(ElidedLabel(where, "mono"))
        top.addLayout(names, 1)
        card.body.addLayout(top)
        running = self._facade.status().profile_path == str(path)
        actions = QHBoxLayout()
        in_game = primary("  Настроить в игре", lambda: self._open_editor(path, "game"))
        in_game.setIcon(theme.icon("crosshair", theme.ON_ACCENT))
        in_game.setEnabled(running)
        in_game.setToolTip("" if running else "Запустите игру — редактор откроется поверх неё")
        actions.addWidget(in_game)
        screenshot = QPushButton("  По скриншоту")
        screenshot.setIcon(theme.icon("image"))
        screenshot.clicked.connect(lambda: self._open_editor(path, "screenshot"))
        actions.addWidget(screenshot)
        actions.addStretch(1)
        export = QPushButton("Экспорт")
        export.clicked.connect(lambda: self._export(path))
        actions.addWidget(export)
        delete = QPushButton("Удалить")
        delete.clicked.connect(lambda: self._delete(path, title))
        actions.addWidget(delete)
        card.body.addLayout(actions)
        return card

    def _zones(self, path: Path, profile: Profile) -> QWidget:
        aspects = sorted(profile.layouts) or []
        status = self._facade.status()
        if self._aspect not in aspects:
            running_aspect = status.aspect if status.profile_path == str(path) else None
            self._aspect = (
                running_aspect if running_aspect in aspects else (aspects[0] if aspects else None)
            )
        card = Card()
        head = QHBoxLayout()
        head.addWidget(label(f"ЗОНЫ · {self._aspect or '—'}", "sectionTitle"), 1)
        if len(aspects) > 1:
            choose = combo([(a, a) for a in aspects], self._aspect, "Раскладка")
            choose.currentIndexChanged.connect(
                lambda _i: self._set_aspect(path, str(choose.currentData()))
            )
            head.addWidget(choose)
        head.addWidget(label("Изменяются в редакторе зон", "hint"))
        card.body.addLayout(head)
        zones = profile.layouts[self._aspect].zones if self._aspect else []
        if not zones:
            card.body.addWidget(
                label(
                    "Зон нет. Откройте редактор зон в игре или по скриншоту.", "caption", wrap=True
                )
            )
        for index, zone in enumerate(zones):
            line = QHBoxLayout()
            color = theme.ZONE_COLORS[index % len(theme.ZONE_COLORS)]
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color};")
            line.addWidget(dot)
            names = QVBoxLayout()
            names.setSpacing(0)
            names.addWidget(label(zone.name or zone.id))
            names.addWidget(label(zone_behaviour(zone), "hint"))
            line.addLayout(names, 1)
            if zone.policy is not None:
                voice = zone.policy.voice
                text = VOICE_MODE_TEXT[voice.mode]
                if voice.mode == "fixed":
                    text = voice_text(voice.id, self._facade.voices())
                line.addWidget(label(text, "caption"))
            card.body.addLayout(line)
        return card

    def _set_aspect(self, path: Path, aspect: str) -> None:
        self._aspect = aspect
        self._show_profile(path)

    def _voices(self, path: Path, profile: Profile) -> QWidget:
        card = Card("Голоса профиля")
        voices = self._facade.voices()
        rows = (
            ("Рассказчик", profile.narrator_voice, "narrator_voice"),
            ("Мужской по умолчанию", profile.default_voices.male, "male"),
            ("Женский по умолчанию", profile.default_voices.female, "female"),
        )
        for title, value, key in rows:
            line = QHBoxLayout()
            line.addWidget(label(title), 1)
            choose = combo(voice_items(voices), value, title)
            choose.currentIndexChanged.connect(
                lambda _i, c=choose, k=key: self._set_voice(path, k, str(c.currentData()))
            )
            line.addWidget(choose, 1)
            line.addWidget(
                icon_button(
                    "play", "Прослушать", lambda c=choose: self._listen(str(c.currentData()))
                )
            )
            card.body.addLayout(line)
        return card

    def _characters(self, path: Path, profile: Profile) -> QWidget:
        card = Card()
        head = QHBoxLayout()
        head.addWidget(label(f"ПЕРСОНАЖИ · {len(profile.characters)}", "sectionTitle"), 1)
        importer = QPushButton("Импорт списка")
        importer.clicked.connect(lambda: self._import_characters(path))
        head.addWidget(importer)
        add = primary("Добавить", lambda: self._edit_character(path, None))
        head.addWidget(add)
        card.body.addLayout(head)
        voices = self._facade.voices()
        table = QTableWidget(len(profile.characters), 4)
        table.setHorizontalHeaderLabels(["ПЕРСОНАЖ", "ГОЛОС", "НАЗНАЧЕН", ""])
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = table.horizontalHeader()
        header.setMinimumSectionSize(60)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 140), (2, 124), (3, 112)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(column, width)
        for row_index, character in enumerate(profile.characters):
            name = QWidget()
            name_box = QVBoxLayout(name)
            name_box.setContentsMargins(6, 2, 6, 2)
            name_box.setSpacing(0)
            name_box.addWidget(label(character.name))
            details = [gender_text(character.gender)]
            if character.aliases:
                details.append("также: " + ", ".join(character.aliases))
            hint = label(" · ".join(details), "hint")
            hint.setToolTip(" · ".join(details))
            name_box.addWidget(hint)
            table.setCellWidget(row_index, 0, name)
            voice = voice_text(character.voice, voices) if character.voice else "ещё не звучал"
            table.setCellWidget(row_index, 1, label(f"  {voice}  ", "caption"))
            chip_box = QWidget()
            chip_layout = QHBoxLayout(chip_box)
            chip_layout.setContentsMargins(6, 0, 6, 0)
            chip_layout.addWidget(assigned_chip(character), 0, Qt.AlignmentFlag.AlignLeft)
            table.setCellWidget(row_index, 2, chip_box)
            actions = QWidget()
            buttons = QHBoxLayout(actions)
            buttons.setContentsMargins(4, 0, 4, 0)
            buttons.setSpacing(2)
            buttons.addWidget(
                icon_button(
                    "play", "Прослушать", lambda c=character: self._listen_character(path, c)
                )
            )
            buttons.addWidget(
                icon_button("pencil", "Изменить", lambda c=character: self._edit_character(path, c))
            )
            more = QPushButton("⋯")
            more.setObjectName("iconButton")
            more.setToolTip("Ещё")
            more.setAccessibleName(f"Ещё: {character.name}")
            menu = QMenu(more)
            menu.addAction(theme.icon("merge"), "Объединить…").triggered.connect(
                lambda _=False, c=character: self._merge(path, profile, c)
            )
            menu.addAction(theme.icon("trash-2"), "Удалить").triggered.connect(
                lambda _=False, c=character: self._delete_character(path, c)
            )
            more.setMenu(menu)
            buttons.addWidget(more)
            table.setCellWidget(row_index, 3, actions)
            table.setRowHeight(row_index, 52)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setFixedHeight(
            table.horizontalHeader().sizeHint().height() + 52 * len(profile.characters) + 4
        )
        card.body.addWidget(table)
        if not profile.characters:
            card.body.addWidget(
                label(
                    "Персонажи появятся сами с первой репликой с именем — или импортируйте список.",
                    "hint",
                    wrap=True,
                )
            )
        return card

    def _stoplist(self, path: Path, profile: Profile) -> QWidget:
        frame = QFrame()
        line = QHBoxLayout(frame)
        line.setContentsMargins(4, 0, 4, 0)
        words = ", ".join(w.capitalize() for w in profile.speaker_stoplist[:5])
        more = "…" if len(profile.speaker_stoplist) > 5 else ""
        line.addWidget(label(f"Не считать именами: {words}{more}", "caption", wrap=True), 1)
        change = QPushButton("Изменить")
        change.clicked.connect(lambda: self._edit_stoplist(path, profile))
        line.addWidget(change)
        return frame

    def _fill_dictionary(self, path: Path, profile: Profile) -> None:
        body = self._dictionary.body
        clear_layout(body)
        body.addWidget(label("ПРОИЗНОШЕНИЕ", "sectionTitle"))
        body.addWidget(label("«+» — ударение, «*» — все формы слова", "hint", wrap=True))
        for index, entry in enumerate(profile.pronunciation):
            line = QHBoxLayout()
            text = label(f"{entry.from_}  →  {entry.to}", "mono")
            text.setWordWrap(True)
            line.addWidget(text, 1)
            line.addWidget(
                icon_button(
                    "play", "Прослушать", lambda e=entry: self._listen_entry(e, e.from_.rstrip("*"))
                )
            )
            line.addWidget(
                icon_button("pencil", "Изменить", lambda i=index: self._edit_entry(path, i))
            )
            line.addWidget(
                icon_button("trash-2", "Удалить", lambda i=index: self._delete_entry(path, i))
            )
            body.addLayout(line)
        add = QPushButton("  Добавить замену")
        add.setIcon(theme.icon("plus"))
        add.clicked.connect(lambda: self._edit_entry(path, None))
        body.addWidget(add)

    # ------------------------------------------------------------------ actions

    def _change(self, path: Path, change: Callable[[Profile], Profile | None]) -> None:
        try:
            self._facade.update_profile(path, change)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(
                self,
                "Не сохранено",
                format_error(exc) if not isinstance(exc, KeyError) else str(exc),
            )
        self._show_profile(path)

    def _set_voice(self, path: Path, key: str, voice: str) -> None:
        def change(p: Profile) -> Profile:
            if key == "narrator_voice":
                return p.model_copy(update={"narrator_voice": voice})
            defaults = p.default_voices.model_copy(update={key: voice})
            return p.model_copy(update={"default_voices": defaults})

        self._change(path, change)

    def _listen(self, voice: str, name: str = "") -> None:
        if not voice:
            return
        hello = f"Привет! Меня зовут {name}." if name else "Привет! Так звучит этот голос."
        self._facade.say(hello, voice)

    def _listen_character(self, path: Path, character: Character) -> None:
        profile = self._facade.profile(path)
        voice = character.voice or {
            Gender.MALE: profile.default_voices.male,
            Gender.FEMALE: profile.default_voices.female,
        }.get(character.gender, profile.narrator_voice)
        self._listen(voice, character.name)

    def _edit_character(self, path: Path, character: Character | None) -> None:
        dialog = CharacterDialog(self, self._facade.voices(), self._listen, character)
        if not dialog.exec():
            return
        updated = dialog.result_character()
        if updated is None:
            return

        def change(p: Profile) -> Profile:
            characters = list(p.characters)
            if character is None:
                characters.append(updated)
            else:
                characters = [updated if c.name == character.name else c for c in characters]
            return p.model_copy(update={"characters": characters})

        self._change(path, change)

    def _merge(self, path: Path, profile: Profile, character: Character) -> None:
        names = [c.name for c in profile.characters]
        if len(names) < 2:
            return
        dialog = MergeDialog(self, character.name, names)
        if dialog.exec() and dialog.target():
            try:
                self._facade.merge_characters(path, character.name, dialog.target())
            except (LookupError, ValueError) as exc:
                QMessageBox.warning(self, "Не объединено", str(exc))
            self._show_profile(path)

    def _delete_character(self, path: Path, character: Character) -> None:
        answer = QMessageBox.question(self, "Удалить персонажа", f"Удалить «{character.name}»?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._change(
            path,
            lambda p: p.model_copy(
                update={"characters": [c for c in p.characters if c.name != character.name]}
            ),
        )

    def _import_characters(self, path: Path) -> None:
        dialog = ImportDialog(self)
        if not dialog.exec():
            return
        try:
            result = self._facade.import_characters_text(path, dialog.text.toPlainText())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Импорт не выполнен", format_error(exc))
            return
        report = result.report
        skipped = sorted([*result.problems, *report.skipped], key=lambda p: p.line)
        lines = [
            f"Добавлено: {len(report.added)}",
            f"Обновлено: {len(report.updated)}",
            f"Пропущено: {len(skipped)}",
            *[f"  строка {p.line}: {p.text} — {p.reason}" for p in skipped[:15]],
        ]
        QMessageBox.information(self, "Импорт персонажей", "\n".join(lines))
        self._show_profile(path)

    def _edit_stoplist(self, path: Path, profile: Profile) -> None:
        dialog = StoplistDialog(self, profile.speaker_stoplist)
        if dialog.exec():
            words = dialog.words()
            self._change(path, lambda p: p.model_copy(update={"speaker_stoplist": words}))

    def _listen_entry(self, entry: PronunciationRule, phrase: str) -> None:
        if self._selected is None:
            return
        voice = self._facade.profile(self._selected).narrator_voice
        self._facade.say(phrase, voice, pronunciation=((entry.from_, entry.to, entry.regex),))

    def _edit_entry(self, path: Path, index: int | None) -> None:
        profile = self._facade.profile(path)
        current = profile.pronunciation[index] if index is not None else None
        dialog = EntryDialog(self, self._listen_entry, current)
        if not dialog.exec():
            return
        entry = dialog.entry()
        if entry is None:
            return

        def change(p: Profile) -> Profile:
            rules = list(p.pronunciation)
            if index is None or index >= len(rules):
                rules.append(entry)
            else:
                rules[index] = entry
            return p.model_copy(update={"pronunciation": rules})

        self._change(path, change)

    def _delete_entry(self, path: Path, index: int) -> None:
        def change(p: Profile) -> Profile:
            rules = [r for i, r in enumerate(p.pronunciation) if i != index]
            return p.model_copy(update={"pronunciation": rules})

        self._change(path, change)

    def _export(self, path: Path) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, "Экспорт профиля", path.name, "Профиль (*.json)"
        )
        if name:
            try:
                self._facade.export_profile(path, Path(name))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Не сохранено", format_error(exc))

    def _delete(self, path: Path, title: str) -> None:
        answer = QMessageBox.question(
            self, "Удалить профиль", f"Удалить профиль «{title}»? Сама игра не удаляется."
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._facade.delete_profile(path)
            self._selected = None
            self.refresh()
