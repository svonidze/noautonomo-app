# Понятный каталог резервных копий и полное disaster recovery

## Summary

Целевой файл плана: `docs/plans/2026-08-18-backup-catalog-and-disaster-recovery.md`.

Сохранить существующее клиентское шифрование `rclone crypt`: имена и содержимое объектов в Yandex Object Storage остаются нечитаемыми без crypt-конфигурации. Вместо раскрытия структуры в S3 добавить:

- единый формат backup + manifest;
- CLI для просмотра, получения, проверки и restore drill;
- read-only экран «Резервные копии» в веб-интерфейсе;
- подробный runbook восстановления локальной БД, private root и полностью потерянного сервера;
- обязательное внешнее хранение crypt-конфигурации.

## Backup format and catalog

- Оставить backup парой файлов с одинаковым UTC timestamp:
  - `private-root-20260818T074838.261643Z.tar.gz`;
  - `private-root-20260818T074838.261643Z.manifest.json`.
- Использовать стабильный идентификатор `daily:20260818T074838.261643Z` или `monthly:…`. Идентификатор валидируется строгим шаблоном и никогда не превращается напрямую в произвольный файловый путь.
- Ввести manifest format 2 с полями:
  - `format`, `backup_id`, `backup_class`;
  - `created_at_utc`, `source_release_sha`, `database_schema_version`;
  - `archive.name`, `archive.size_bytes`, `archive.sha256`;
  - список файлов с путём, размером и SHA-256;
  - версия backup-инструмента.
- Restore-код обязан продолжать читать format 1; существующие backups не переписываются и не удаляются.
- Добавить модуль каталога, который:
  - получает расшифрованный список через `yandex-daily-crypt:` и `yandex-monthly-crypt:`;
  - объединяет archive и manifest по timestamp;
  - читает manifest через crypt remote;
  - определяет статусы `ready`, `archive_missing`, `manifest_missing`, `manifest_invalid`, `remote_unavailable`;
  - никогда не возвращает raw encrypted object names, access keys, crypt passwords или содержимое `rclone.conf`.
- Каноническое время хранить в UTC. CLI и веб показывают также Europe/Madrid и относительный возраст: «сегодня», «вчера», «2 дня назад».
- Не создавать открытый `catalog.json` рядом с raw S3 objects: даты, имена и структура остаются зашифрованными.

## CLI and web interfaces

- Расширить `autonomo-tax backup` командами:
  - `list [--class daily|monthly] [--json]`;
  - `inspect <backup_id>`;
  - `fetch <backup_id> --out <empty-directory>`;
  - `verify <backup_id>` — скачать пару, проверить archive SHA-256 и manifest;
  - `drill <backup_id> --target-root <empty-directory>` — fetch, verify, extract, SQLite integrity/FK/schema checks.
- `drill` не меняет production private root и не останавливает production service. После успешной проверки он атомарно записывает приватный marker последнего drill с backup ID, временем и результатом.
- Production cutover оставить только в `ops/restore.sh` с явным флагом подтверждения. Веб-интерфейс не получает операцию замены production БД.
- Добавить `GET /api/backups`:
  - `generated_at`, `timezone`, состояние crypt remote;
  - latest daily/monthly;
  - последний restore drill;
  - массив backup entries с ID, типом, UTC/local временем, возрастом, размерами, schema/release и статусом пары.
- Добавить read-only раздел «Резервные копии»:
  - карточки «Последний daily», «Последний monthly», «Последний restore drill», «Шифрование»;
  - таблица: дата, тип, возраст, размер, schema, release, расположение, состояние;
  - фильтры daily/monthly и ready/problematic;
  - кнопки «Обновить» и «Показать команды восстановления»;
  - без download archive через браузер и без отображения серверных путей/секретов.
- Для этого раздела скрывать period selector и кнопку «Добавить»: backups не относятся к налоговому периоду.
- Поддержать RU/EN, keyboard navigation, loading/empty/error states и мобильную таблицу-карточки.
- Доступ оставить через существующую Tailscale session и same-origin защиту. Поскольку v1 read-only, отдельная admin role не требуется.

## Disaster recovery documentation

- Добавить `docs/backup-format.md`:
  - почему в Yandex Console видны случайные имена;
  - что именно находится в archive и manifest;
  - как timestamp связывает пару;
  - разница daily/monthly/evidence;
  - manifest format 1 и 2;
  - что проверяет каждая команда.
- Добавить `docs/disaster-recovery.md` с точными сценариями:
  1. Повреждена только SQLite на живом сервере.
  2. Потерян весь private root.
  3. Полностью потерян сервер.
  4. Недоступен основной Google Drive.
  5. Утрачен `rclone.conf`.
- Для полного восстановления описать последовательность:
  1. Развернуть чистый host и release по immutable SHA.
  2. Восстановить crypt-конфигурацию из password manager.
  3. Выпустить новый Yandex static key при необходимости.
  4. Проверить права и crypt remotes.
  5. Выполнить `backup list`, выбрать backup по локальной дате и статусу.
  6. Выполнить `backup drill`.
  7. Проверить web service на альтернативном private root/порту.
  8. Создать safety snapshot текущего состояния, если оно существует.
  9. Выполнить production restore.
  10. Проверить health endpoint, schema, attachments и обе storage replicas.
- Явно зафиксировать: S3 static key можно перевыпустить, но crypt password/salt невосстановимы. Потеря единственной копии crypt-конфигурации означает потерю всех S3 backups.
- Обязать хранить актуальный `rclone.conf` как защищённое вложение в password manager или как `age`-зашифрованный recovery kit вне сервера, bucket и Git.
- Добавить безопасную команду readiness-check, которая проверяет:
  - режим файла `0600`;
  - наличие daily/monthly/evidence crypt remotes;
  - возможность расшифрованного list/read;
  - наличие готовой archive/manifest пары;
  - свежесть daily/monthly;
  - не выводит конфигурацию и секреты.
- Обновить `ops/README.md` короткой ссылкой на оба документа и ежемесячный drill checklist.

## Tests and acceptance

- Unit tests:
  - создание manifest v2;
  - чтение v1 и v2;
  - pairing archive/manifest;
  - daily/monthly ID parsing;
  - UTC → Europe/Madrid, включая DST;
  - missing/corrupt manifest и orphan archive;
  - отсутствие secrets/raw encrypted names в API и ошибках.
- CLI integration tests с временным local crypt remote:
  - `list`, `inspect`, `fetch`, `verify`, `drill`;
  - неверный crypt config;
  - повреждённый archive;
  - непустой target directory;
  - SQLite integrity/FK/schema failure.
- Web tests:
  - `/api/backups` требует сессию;
  - корректные ready/problematic состояния;
  - экран показывает «вчера/позавчера» и точное локальное время;
  - period controls скрыты;
  - API не предоставляет production restore или archive download.
- Production acceptance:
  - существующие format-1 backups отображаются;
  - новый format-2 daily backup появляется с понятной датой;
  - remote `verify` проходит;
  - restore drill в пустую директорию проходит;
  - production service остаётся активным;
  - результат drill отображается в вебе;
  - privacy guard и полный test suite проходят.

## Rollout defaults

- Raw S3 layout и crypt remotes не менять.
- Старые backups не переименовывать и не удалять.
- Сначала задеплоить backward-compatible reader и веб-каталог, затем начать выпуск manifest v2.
- Production restore остаётся CLI-only.
- Полная проверка archive выполняется только явно; обычный web list читает список и manifest, но не скачивает каждый 60+ MB archive.
- Если remote временно недоступен, экран показывает последнее известное локальное состояние как stale, не объявляя backup повреждённым.

## From chat
Это `rclone crypt`: в Yandex видны не реальные имена, а зашифрованные object keys. Поэтому строки вида `8oln2…` — нормальны: в них лежат зашифрованные пары:

- архив `private-root-…tar.gz` — примерно 66 MB;
- manifest `private-root-….manifest.json` — примерно 400 KB.

Когда обращаться к тому же bucket через настроенный `yandex-daily-crypt:` remote, rclone расшифровывает имена и содержимое обратно. В консоли Yandex читать эти объекты невозможно и не нужно.

Описание есть, но сейчас оно недостаточно практичное:

- На `master` его ещё нет; оно находится в draft PR/ветке storage.
- В связанном проекте `spain-autonomo-taxes-storage` файл `ops/README.md` объясняет состав backup и приводит команды restore.
- Скрипт `ops/restore.sh` в том же проекте проверяет manifest, SHA-256 архива и файлы внутри него, затем восстанавливает в пустую директорию.

Главный недочёт runbook: нет явной инструкции «катастрофа с потерей сервера». Для неё нужны не только S3-объекты, но и сохранённый вне сервера `rclone.conf` с crypt-паролями/солью. Без него данные в bucket останутся криптографически нечитабельными. Текущая документация говорит хранить encryption identities в password manager, но не описывает конкретный export/recovery этого конфига.

Нормальный end-to-end процесс должен выглядеть так:

```bash
# 1. На новом хосте восстановить rclone.conf из password manager
# 2. Скачать и расшифровать выбранный backup
rclone --config /secure/rclone.conf copy \
  yandex-daily-crypt: /secure/recovery/backup

# 3. Проверенно распаковать private root
ops/restore.sh --yes-restore-archive \
  /secure/recovery/backup/private-root-....tar.gz \
  /secure/recovery/backup/private-root-....manifest.json \
  /secure/recovery/restored-private-root
```

После этого проверяется SQLite и сервис запускается на альтернативном порту до замены production root.

Я бы обязательно добавил `docs/DISASTER_RECOVERY.md`: таблицу «что где хранится», экспорт/хранение rclone crypt identity, точные команды download → verify → restore → cutover и drill checklist. Это важнее веб-настройки: иначе backup существует, но восстановление не вполне операционно воспроизводимо.
