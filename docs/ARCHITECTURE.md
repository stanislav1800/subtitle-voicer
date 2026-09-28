# Архитектура и структура проекта

Как устроен код Subtitle Voicer: слои, правила зависимостей, дерево папок и рецепты расширения. Что делает приложение — `docs/TZ.md`, как выглядит — `docs/UI.md`.

> **Для исполнителя.** Структура ниже — целевая. Папки и файлы создаются по мере этапов (этап 0 — только каркас пакетов и правила зависимостей). Не добавлять новые верхнеуровневые пакеты и не нарушать правила зависимостей (раздел 3) без правки этого файла и согласования с пользователем.

---

## 1. Что взято из аналогов

Изучены: **LunaTranslator** (Python; хук/OCR → перевод → TTS для игр), **OwOCR** (Python; много OCR-движков), **Translumo** (C#; OCR → перевод → TTS поверх игр), **GameSentenceMiner** (Python; OCR и хуки для игр).

**Хорошие приёмы — берём:**

| Приём | Где увидели | Как у нас |
|---|---|---|
| **Папка на семейство движков, файл на движок, общий базовый интерфейс** | LunaTranslator: `ocrengines/`, `tts/`, `translator/` — в каждой `base*.py` и по файлу на движок | `engines/<семейство>/<движок>.py`, интерфейс — в `core/ports.py` |
| **Выбор движка по id из настроек через ленивый импорт** | LunaTranslator: `importlib.import_module("tts." + use)` | Реестр `id → "модуль:Класс"`, импорт только выбранного движка |
| **Метаданные движка и мягкая недоступность**: имя, возможности, флаг «доступен», тяжёлые зависимости импортируются внутри движка, при их отсутствии — предупреждение, а не падение | OwOCR: `readable_name`, `available`, `EngineCapabilities`, `_import_dependencies()` | `id`, `display_name`, `capabilities`, `is_available()`, тяжёлый импорт только в `load()` |
| **Общие типизированные модели результатов**, одинаковые для всех движков | OwOCR: `BoundingBox`, `Line`, `OcrResult`; GameSentenceMiner: `text_pipeline/models.py` | `core/models.py` |
| **Источники текста отдельно от обработки**: хук, OCR, буфер обмена — разные «источники» с общим интерфейсом | LunaTranslator: `textio/textsource/` (`ocrtext`, `texthook`, `copyboard`) и `textio/textoutput/` | `sources/` — сейчас OCR по зонам и ручное чтение, потом хуки; выход — через шину событий |
| **Слои как отдельные модули с интерфейсами и фабриками**: OCR, TTS, обработка, инфраструктура, UI отдельно | Translumo: проекты `Translumo.OCR`, `.TTS`, `.Processing`, `.Infrastructure`, UI отдельно; `IOCREngine` + `OcrEnginesFactory` | Слои раздела 2 и правила зависимостей раздела 3 |
| **Мелкие сервисы на каждую заботу**: проверка «похоже ли на текст», кеш-дедупликация | Translumo: `TextValidityPredictor`, `TextResultCacheService` | Фильтр мусора, стабилизатор, сопоставление персонажей — отдельные классы в `pipeline/` |
| **Данные по умолчанию — файлами, а не в коде** | LunaTranslator: `defaultconfig/*.json` | `resources/`: шаблоны зон, стоп-лист, таблицы транслитерации и т.п. |
| **Тяжёлые движки — необязательные зависимости** | OwOCR: `[project.optional-dependencies]` на каждый движок | Extras для поздних движков (Qwen3-TTS, хуки) |

**Плохие приёмы — не повторяем:**

| Приём | Где увидели | Почему плохо / что вместо |
|---|---|---|
| Все движки в одном огромном файле | OwOCR: `ocr.py` с десятками классов | Трудно читать и менять → файл на движок |
| Главный файл на тысячи строк, где собрано всё | LunaTranslator: `LunaTranslator.py` | → тонкая «точка сборки» `app/container.py`, логика по модулям |
| Глобальный конфиг, который импортируют все модули | LunaTranslator: `from myutils.config import globalconfig` | Скрытые связи, тесты зависят от файлов → настройки передаются в конструктор |
| Логика, перемешанная с потоками и UI | частично во всех | → «чистое ядро, оболочка снаружи» (раздел 2) |

---

## 2. Слои

Принцип — **чистое ядро, оболочка снаружи** (functional core, imperative shell) плюс **порты и адаптеры**:

```
            ┌──────────────────────────── ui/ (PySide6) ──────────────┐
            │                      cli.py                             │
            └──────────────┬──────────────────────────────────────────┘
                           │ фасад и события
            ┌──────────────▼──────────── app/ ────────────────────────┐
            │ container (сборка), runtime (жизненный цикл), bus       │
            └───────┬────────────────────────┬────────────────────────┘
                    │                        │
     ┌──────────────▼─────── services/ ──┐   │  ┌──── engines/ ─────────────┐
     │ потоки и очереди: захват, зоны,   │   │  │ адаптеры внешнего мира:   │
     │ синтез, плеер, ducking, watcher   │   │  │ WGC, RapidOCR, Silero,    │
     │ sources/: откуда берётся текст    │   └─►│ sounddevice, pycaw, WinAPI│
     └──────────────┬────────────────────┘      └────────────┬──────────────┘
                    │ вызывает                              │ реализует
     ┌──────────────▼──────── pipeline/ ─────┐   ┌───────────▼──── core/ ─────┐
     │ алгоритмы без потоков и ввода-вывода: │──►│ модели, события, порты     │
     │ маски, стабилизатор, говорящий,       │   │ (Protocol), ошибки         │
     │ подготовка текста, планировщик        │   └────────────────────────────┘
     └───────────────────────────────────────┘          ▲
     ┌──────────────── config/ ──────────────┐          │
     │ настройки, профили, хранение          │──────────┘
     └───────────────────────────────────────┘
```

| Слой | Что внутри | Что нельзя |
|---|---|---|
| `core/` | Модели данных, события, порты (`Protocol`), ошибки. Зависит только от stdlib и `numpy` | Импортировать что-либо из проекта, Windows-модули, Qt |
| `config/` | pydantic-модели настроек и профилей, хранение на диске, пути, загрузка `resources/` | Потоки, движки, Qt |
| `pipeline/` | **Чистые алгоритмы**: принимают данные и порты, возвращают результат. Время — только через порт `Clock` | Потоки, `time.sleep`, `time.monotonic()` напрямую, файлы, Windows, Qt, конкретные движки |
| `engines/` | **Адаптеры** к внешним библиотекам и ОС; каждый реализует порт из `core/ports.py` | Импортировать `pipeline/`, `services/`, `app/`, `ui/`; бизнес-логику |
| `sources/` | Откуда берутся реплики: OCR по зонам, ручное чтение; позже — хуки | Qt, конкретные движки (только порты) |
| `services/` | **Оболочка**: потоки, очереди, циклы; вызывают `pipeline/`, публикуют события в шину | Qt; импорт конкретных движков (движки приходят готовыми из `app/`) |
| `app/` | **Точка сборки**: создаёт движки по настройкам через реестры, связывает сервисы, запускает и останавливает | Алгоритмы; Qt |
| `ui/` | Окна и виджеты PySide6. Общается с приложением через фасад `app` и события шины | Импортировать `engines/`, `services/`, `pipeline/` напрямую |
| `cli.py`, `__main__.py` | Точки входа | Логика |

---

## 3. Правила зависимостей

Разрешённые направления импорта (стрелка — «может импортировать»):

```
ui        → app, config, core
cli       → app, config, core
app       → services, sources, engines, pipeline, config, core
services  → sources, pipeline, config, core
sources   → pipeline, config, core
pipeline  → config, core
engines   → core
config    → core
core      → (ничего из проекта)
```

- **Проверяются автоматически** инструментом `import-linter` (dev-зависимость; контракты в `pyproject.toml`, секция `[tool.importlinter]`). Запуск: `uv run lint-imports`. Нарушение контракта = красный CI и незакрытый этап.
- Модули, которые трогают Windows (`pywin32`, `comtypes`, `pycaw`, `winrt`, `windows_capture`, `winreg`), допустимы **только** в `engines/`. Это тоже контракт `import-linter` (forbidden для остальных слоёв).
- `PySide6` допустим **только** в `ui/`.

---

## 4. Дерево

```
subtitle-voicer/
├── CLAUDE.md
├── README.md
├── pyproject.toml                 # зависимости, extras, ruff, pytest, import-linter
├── docs/
│   ├── TZ.md                      # что делаем
│   ├── ARCHITECTURE.md            # как устроен код (этот файл)
│   ├── UI.md                      # как выглядит
│   └── design/                    # HTML-макеты экранов
├── src/subvoicer/
│   ├── __init__.py
│   ├── __main__.py                # запуск GUI (этап 6)
│   ├── cli.py                     # CLI: watch / run / characters (argparse)
│   │
│   ├── core/
│   │   ├── models.py              # Rect, Frame, Mask, OcrLine, OcrResult, Utterance, SpeechRequest,
│   │   │                          # Voice, AudioClip, Priority, ZoneTrigger …
│   │   ├── events.py              # UtteranceDetected, SpeechStarted/Finished/Dropped, ProfileActivated,
│   │   │                          # GameDetected, ZoneStateChanged, EngineUnavailable …
│   │   ├── ports.py               # FrameSource, OcrEngine, TtsEngine, StressMarker, AudioOutput, Ducker,
│   │   │                          # HotkeyService, WindowInfo, GameLibrarySource, TextSource, Clock,
│   │   │                          # EventPublisher
│   │   └── errors.py
│   │
│   ├── config/
│   │   ├── paths.py               # %APPDATA%/SubtitleVoicer/… через platformdirs
│   │   ├── settings.py            # Settings (pydantic) + чтение/запись settings.toml
│   │   ├── profile.py             # Profile, Layout, Zone, SpeakerConfig, VoiceConfig, Character,
│   │   │                          # PronunciationEntry (pydantic, 5.4 ТЗ)
│   │   ├── profile_store.py       # файлы профилей, сопоставление процесса с профилем (4.11), импорт/экспорт
│   │   ├── characters_csv.py      # импорт персонажей из CSV (4.6)
│   │   └── resources.py           # загрузка файлов из resources/
│   │
│   ├── pipeline/                  # чистые алгоритмы, всё тестируется без Windows
│   │   ├── vision/
│   │   │   ├── crop.py            # вырезка зоны по долям кадра
│   │   │   ├── masks.py           # пресеты color / bright_on_dark / dark_on_bright / adaptive
│   │   │   └── change.py          # изменение и пустота маски
│   │   ├── ocr/
│   │   │   ├── inputs.py          # три варианта входа OCR (4.3)
│   │   │   ├── runner.py          # вызов OcrEngine, варианты при низкой уверенности, порядок строк
│   │   │   └── junk_filter.py     # фильтр мусора
│   │   ├── stabilizer.py          # автомат 4.4 + память repeat_cooldown
│   │   ├── speaker/
│   │   │   ├── extract.py         # prefix / zone / color → кандидат
│   │   │   ├── validate.py        # проверка имени, стоп-лист
│   │   │   ├── characters.py      # сопоставление, назначение голосов, уточнение пола, объединение
│   │   │   └── gender.py          # эвристика пола
│   │   ├── text/
│   │   │   ├── preparer.py        # порядок шагов 4.7
│   │   │   ├── pronunciation.py   # словарь со звёздочкой и регулярками
│   │   │   ├── numbers.py
│   │   │   ├── latin.py           # аббревиатуры и транслитерация
│   │   │   ├── stress.py          # защита ручных «+» вокруг вызова порта StressMarker
│   │   │   ├── cleanup.py
│   │   │   └── sentences.py
│   │   ├── speech/
│   │   │   ├── scheduler.py       # политика 4.9: приоритеты, очередь, обрывы, ускорение
│   │   │   └── cache.py           # кеш синтеза
│   │   └── games/
│   │       ├── matching.py        # полноэкранность, исключения, «уже спрашивали» (4.11)
│   │       └── aspect.py          # соотношение сторон кадра
│   │
│   ├── sources/                   # откуда берутся реплики (TextSource)
│   │   ├── zone_ocr.py            # зоны auto: кадр → маска → стабилизатор → OCR → говорящий → Utterance
│   │   ├── manual.py              # зоны по хоткею, «выделить и прочитать», «повторить»
│   │   └── hook.py                # этап 11 (LunaHook)
│   │
│   ├── engines/                   # адаптеры; только здесь Windows и сторонние движки
│   │   ├── registry.py            # общий механизм реестров (раздел 5)
│   │   ├── capture/
│   │   │   ├── __init__.py        # CAPTURE = {"wgc": …, "dxgi": …}
│   │   │   ├── wgc.py             # windows-capture
│   │   │   └── dxgi.py
│   │   ├── ocr/
│   │   │   ├── __init__.py        # OCR = {"rapidocr": …, "winrt": …}
│   │   │   ├── rapid.py
│   │   │   └── winrt.py
│   │   ├── tts/
│   │   │   ├── __init__.py        # TTS = {"silero": …, "sapi5": …, "qwen3": …}
│   │   │   ├── silero.py
│   │   │   ├── silero_stress.py   # StressMarker: расстановка ударений (silero-stress)
│   │   │   ├── sapi5.py
│   │   │   └── qwen3.py           # этап 10
│   │   ├── audio/
│   │   │   ├── output.py          # sounddevice
│   │   │   └── ducker.py          # pycaw + ducking_state.json
│   │   ├── system/
│   │   │   ├── windows.py         # окно переднего плана, процессы, мониторы, DPI (WindowInfo)
│   │   │   ├── hotkeys.py         # pynput (HotkeyService)
│   │   │   ├── autostart.py       # ключ реестра Run
│   │   │   └── icons.py           # иконка из exe
│   │   └── library/               # этап 9
│   │       ├── __init__.py        # LIBRARY = {"steam": …, "epic": …, "gog": …}
│   │       ├── steam.py
│   │       ├── epic.py
│   │       └── gog.py
│   │
│   ├── services/                  # потоки и очереди
│   │   ├── base.py                # Service: start/stop, поток с перехватом исключений и логом
│   │   ├── capture_service.py     # держит FrameSource, отдаёт последний кадр
│   │   ├── zone_service.py        # цикл poll_hz по зонам auto (через sources/zone_ocr)
│   │   ├── speech_service.py      # планировщик + синтез + плеер + ducking
│   │   ├── game_watcher.py        # 1 Гц, переключение профиля, уведомления
│   │   ├── hotkey_service.py      # общие хоткеи и хоткеи зон активного профиля
│   │   └── history.py             # последние реплики и метрики (режим разработчика)
│   │
│   ├── app/
│   │   ├── bus.py                 # потокобезопасная шина событий (publish/subscribe)
│   │   ├── container.py           # сборка: движки из реестров по настройкам → сервисы
│   │   ├── runtime.py             # запуск/остановка, смена профиля, аварийное восстановление громкости
│   │   └── facade.py              # команды для UI и CLI: pause, repeat, skip, save_profile, preview_voice …
│   │
│   ├── ui/                        # только здесь PySide6
│   │   ├── theme.py               # токены цветов и сборка QSS (docs/UI.md)
│   │   ├── fonts/  icons/         # файлы шрифтов и иконок с лицензиями
│   │   ├── bridge.py              # события шины → Qt-сигналы в главном потоке
│   │   ├── widgets/               # toggle.py, segmented.py, chip.py, keycap.py
│   │   ├── tray.py
│   │   ├── notifications.py
│   │   ├── settings/              # window.py, general.py, profiles.py, hotkeys.py, history.py
│   │   ├── zone_editor/           # editor.py, canvas.py, panel.py, screenshot_dialog.py
│   │   ├── line_window.py         # окно реплики и исправление произношения
│   │   └── overlay.py
│   │
│   └── resources/                 # данные по умолчанию (не код)
│       ├── zone_templates.toml    # шаблоны зон (4.2)
│       ├── speaker_stoplist.txt
│       ├── gender_exceptions.txt  # существительные на «-л»
│       ├── short_words.txt        # разрешённые короткие слова фильтра мусора
│       ├── letter_names.toml      # английские буквы кириллицей
│       ├── translit.toml
│       ├── excluded_exes.txt
│       ├── library_ignore.txt     # служебные записи Steam и т.п.
│       └── voices/
│           └── silero_v5.toml     # каталог голосов с полом (4.6)
│
├── tools/                         # скрипты разработчика (не часть приложения)
│   ├── data/phrases_ru.txt
│   ├── gen_synthetic.py
│   ├── ocr_bench.py
│   ├── smoke_stack.py
│   ├── voice_samples.py
│   └── latency_report.py
│
└── tests/
    ├── conftest.py
    ├── fakes/                     # FakeClock, FakeFrameSource, FakeOcr, FakeTts, FakeAudio, FakeDucker,
    │                              # FakeWindowInfo, RecordingBus
    ├── unit/                      # зеркалит src/subvoicer: unit/pipeline/…, unit/config/…
    ├── contracts/                 # общие тесты порта для каждого движка (раздел 6)
    ├── integration/               # связки сервисов на фейках; Windows-тесты помечены @pytest.mark.windows
    └── fixtures/
        ├── synthetic/             # генерируется, не в git
        ├── real/<игра>/
        └── library/               # образцы файлов Steam/Epic/GOG
```

Путь каталога голосов в ТЗ (4.6) — `src/subvoicer/resources/voices/silero_v5.toml`.

---

## 5. Реестр движков

Одинаковый механизм для всех семейств (`capture`, `ocr`, `tts`, `library`):

```python
# engines/tts/__init__.py
TTS: dict[str, str] = {
    "silero": "subvoicer.engines.tts.silero:SileroEngine",
    "sapi5": "subvoicer.engines.tts.sapi5:Sapi5Engine",
    # "qwen3": "subvoicer.engines.tts.qwen3:Qwen3Engine",   # этап 10
}
```

- Ключ — `id` движка из настроек/профиля. Значение — путь к классу; модуль импортируется только когда движок выбран (`engines/registry.py: resolve(family, id)`).
- **Модуль движка лёгкий при импорте**: тяжёлые библиотеки (`torch`, `onnxruntime`, `windows_capture` …) импортируются внутри `load()`.
- **Обязательные атрибуты класса движка:**
  - `id: str`, `display_name: str`;
  - `capabilities` — dataclass возможностей семейства (для TTS: `supports_rate`, `streaming`, `needs_gpu`; для OCR: `languages`, `returns_confidence`; для захвата: `window_capture`);
  - `@classmethod is_available() -> bool` — есть ли нужные библиотеки и ОС, **без** загрузки моделей;
  - `load()` — тяжёлая инициализация; `close()` — освобождение ресурсов.
- **Fallback-цепочка** задаётся в `app/container.py`, а не внутри движков: например, `tts: ["silero", "sapi5"]` — берётся первый, у которого `is_available()` и `load()` прошли; иначе событие `EngineUnavailable` и уведомление пользователю.
- Список движков для выпадающих меню в UI строится из реестра + `is_available()`.

Почему не автоматическое обнаружение плагинов через entry points: движков мало, явный список проще читать и отлаживать. Если понадобятся сторонние плагины — реестр заменяется на entry points без изменения интерфейсов.

---

## 6. Шина событий и потоки

- `app/bus.py` — простая потокобезопасная шина: `publish(event)`, `subscribe(event_type, handler)`. Обработчик вызывается в потоке издателя; всё, что долго, обработчик сам кладёт в свою очередь.
- Сервисы общаются **данными через очереди** (кадр → зоны → реплики → речь) и **сообщают о фактах через шину** (реплика найдена, речь началась, профиль сменился).
- UI подписывается на шину только через `ui/bridge.py`, который перекладывает события в Qt-сигналы главного потока.
- История (режим разработчика), оверлей и будущие «выходы» (например, отправка текста куда-то ещё) — просто подписчики шины. **Новый потребитель текста не требует менять конвейер.**
- Время: всё, что зависит от времени (стабилизатор, TTL очереди, cooldown, ducking), получает `Clock` из `core/ports.py`. В тестах — `FakeClock`, который двигается вручную.

---

## 7. Тесты

- `tests/unit/` зеркалит `src/subvoicer/`: `tests/unit/pipeline/test_stabilizer.py` и т.д.
- `tests/fakes/` — фейки портов, общие для всех тестов. Фейк реализует тот же `Protocol`, что и настоящий движок.
- **Контрактные тесты** (`tests/contracts/`): один набор тестов на порт, параметризованный всеми движками семейства, у которых `is_available()` на этой машине. Пример для TTS: синтез непустой строки даёт непустой float32-звук с правильной частотой; неизвестный голос — понятная ошибка; `rate` меняет длительность, если `supports_rate`. Новый движок автоматически проходит те же проверки.
- `tests/integration/` — сервисы вместе на фейках (например, «две зоны auto с разными приоритетами → порядок озвучки»). Тесты, которым нужна настоящая Windows, — `@pytest.mark.windows`.

---

## 8. Рецепты расширения

**Новый движок TTS (или OCR, захвата, источника библиотеки):**
1. Файл `engines/tts/<имя>.py`, класс реализует `TtsEngine` из `core/ports.py` и атрибуты раздела 5.
2. Строка в реестре `engines/tts/__init__.py`.
3. Если нужны тяжёлые зависимости — extra в `pyproject.toml` (`uv sync --extra <имя>`), импорт внутри `load()`.
4. Для TTS — каталог голосов `resources/voices/<имя>.toml`.
5. Движок проходит контрактные тесты своего семейства.
6. Больше ничего менять не нужно: UI и сборка берут список из реестра.

**Новый источник реплик** (например, хуки на этапе 11): класс в `sources/`, реализует `TextSource`, выдаёт `Utterance` с приоритетом; подключается в `app/container.py`. Планировщик, TTS и UI не меняются.

**Новый шаблон зоны:** запись в `resources/zone_templates.toml`.

**Новое правило подготовки текста:** модуль в `pipeline/text/`, шаг в `preparer.py` в нужном месте порядка (4.7 ТЗ), юнит-тесты.

**Новый потребитель событий** (лог, внешний вывод, статистика): подписчик шины; конвейер не меняется.

---

## 9. Когда что появляется

| Этап | Что создаётся |
|---|---|
| 0 | Весь каркас пакетов (пустые `__init__.py`), `core/` с моделями и портами-заготовками, `app/bus.py`, `engines/registry.py`, контракты `import-linter`, `tests/fakes/FakeClock` |
| 1 | `pipeline/vision/`, `pipeline/ocr/`, `engines/ocr/rapid.py`, `winrt.py` |
| 2 | `engines/capture/`, `pipeline/stabilizer.py`, `pipeline/speaker/extract.py`, `validate.py`, `sources/zone_ocr.py`, `services/capture_service.py`, `zone_service.py`, `cli.py watch` |
| 3 | `engines/tts/`, `engines/audio/output.py`, `pipeline/text/`, `pipeline/speech/`, `services/speech_service.py`, `resources/voices/` |
| 4 | `engines/audio/ducker.py` |
| 5 | `config/profile*.py`, `config/characters_csv.py`, `pipeline/speaker/characters.py`, `pipeline/games/`, `engines/system/windows.py`, `services/game_watcher.py`, `cli.py run / characters` |
| 6 | `ui/`, `app/facade.py`, `services/history.py` |
| 7 | `sources/manual.py`, `engines/system/hotkeys.py` целиком, `services/hotkey_service.py` |
| 8 | загрузка моделей в `config/` + `engines/`, `engines/system/autostart.py` |
| 9 | `engines/library/`, `engines/system/icons.py` |
