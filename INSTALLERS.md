# Нативные установщики

Windows x64: Inno Setup EXE и соседние BIN-части. macOS 14+ Apple Silicon: PKG внутри DMG. Версия 0.3.1. Установка включает Python, зависимости Qwen и FFmpeg, но не модель, пользовательскую библиотеку или ключ лицензии.

## Сборка

Workflow `.github/workflows/installers.yml` собирает обе системы на соответствующих GitHub runners. Запускается вручную или изменением файлов упаковки. Результаты доступны во вкладке Actions → Native installers → Artifacts. Распакуйте весь ZIP Windows перед запуском EXE; BIN нельзя отделять от EXE.

Для локальной Windows-сборки нужны готовый native release, полный onedir engine в `build/sidecar-full/books-engine` и Inno Setup 6.5+. Запуск: `powershell -File scripts/build_windows_installer.ps1 -Compiler "путь к ISCC.exe"`. На Windows macOS-пакет не собирается.

На Apple Silicon после установки зависимостей из `pyproject.toml` и `requirements-tts-macos.txt`: `cargo build --release --locked --manifest-path client/native/Cargo.toml`, затем `python scripts/build_sidecar.py --with-tts --onedir --ffmpeg-dir build/ffmpeg-runtime` и `python scripts/build_macos_installer.py`. Каталог FFmpeg должен содержать бинарник и лицензии. PyInstaller собирает Mach-O библиотеки FFmpeg, чтобы клиенту не требовался Homebrew. Для повторной сборки используйте чистый рабочий каталог.

## Лицензирование

`packaging/license-public.json` содержит только публичный ключ проверки и loopback-адрес существующего сервера владельца. Приватный ключ подписи никогда не упаковывается. GitHub repository variables `BOOKS_LICENSE_URL` и `BOOKS_LICENSE_PUBLIC_KEY` переопределяют конфигурацию при сборке. Для распространения на другие компьютеры необходимо сначала развернуть общий HTTPS-сервер и пересобрать клиент с его адресом. Текущий loopback-пакет не активирует лицензию на новом Mac/PC без сервера.

## Проверки и ограничения

Оба frozen engine проходят импорт FB2/EPUB/PDF, преобразование чисел, проверку авторизации API, обложку, определение языка и корректное завершение. macOS проверяется после переноса в .app. `scripts/verify_windows_installer.ps1` предназначен только для временного CI runner: устанавливает EXE, проверяет установленный engine, удаляет программу и проверяет сохранность пользовательских данных.

В CI модель не скачивается и настоящий синтез не запускается. Это не заменяет ручную проверку микрофона, воспроизведения, системного хранилища ключей, активации и Qwen на чистых Windows/macOS. На Mac по умолчанию используется CPU; MPS и Intel в этот выпуск не входят. Intel требует отдельной совместимой поставки PyTorch/Qwen.

EXE/PKG не подписаны сертификатом издателя; .app имеет только ad-hoc подпись. Для обычного публичного выпуска нужны Windows Authenticode, Apple Developer ID и нотариализация. Сборка не отключает SmartScreen или Gatekeeper.

Удаление программы сохраняет библиотеку: Windows `%APPDATA%/app.localbooks.studio`, macOS `~/Library/Application Support/app.localbooks.studio`, а также отдельно выбранную папку модели. Обновление не должно менять эти данные.
