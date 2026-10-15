# Понятный каталог резервных копий и полное disaster recovery

## Summary

Целевой файл плана: `docs/plans/2026-08-18-backup-catalog-and-disaster-recovery.md`.

Сохранить существующее клиентское шифрование `rclone crypt`: имена и содержимое объектов в Yandex Object Storage остаются нечитаемыми без crypt-конфигурации. Вместо раскрытия структуры в S3 добавить:

- единый формат backup + manifest;
- CLI для просмотра, получения, проверки и restore drill конкретного backup;
- read-only экран «Резервные копии» в веб-интерфейсе.

## Статус на 2026-10-14

Уже сделано и в этом плане не повторяется:

- Runbook [`ops/docs/DISASTER_RECOVERY.md`](../../ops/docs/DISASTER_RECOVERY.md): состав backup, скачивание и проверка пары, изолированный web smoke, пять сценариев восстановления (SQLite, private root, сервер, Google Drive, crypt-конфигурация), production cutover и ежемесячный drill. Там же описано внешнее хранение `rclone.conf` с crypt password/salt.
- Проверка готовности backup при деплое: `ops/backup_readiness.py`.
- Удалённая проверка с изолированным восстановлением и SQLite-проверками для monthly и daily (#126): `ops/verify-backup.sh`, состояние проверки в `ops/backup_state.py`.
- Настройки показывают последний успешный daily/monthly и результат monthly-проверки: `GET /api/settings/backups`, `BackupStatus.vue`.

Не сделано: manifest с идентификатором, классом, release SHA и schema; каталог удалённых пар; команды `backup list/inspect/fetch/verify/drill` по идентификатору; список backups в вебе. Проверка работает только для последней пары класса, а выбор backup при восстановлении остаётся ручным.

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
- Restore-код обязан продолжать читать format 1; существующие backups не переписываются и не удаляются. Сейчас `ops/backup_state.py` отклоняет любой manifest, кроме format 1, поэтому чтение format 2 добавить там до того, как `ops/backup/backup_private_root.py` начнёт его выпускать.
- Добавить модуль каталога, который:
  - получает расшифрованный список через `yandex-daily-crypt:` и `yandex-monthly-crypt:`;
  - объединяет archive и manifest по timestamp;
  - читает manifest через crypt remote;
  - определяет статусы `ready`, `archive_missing`, `manifest_missing`, `manifest_invalid`, `remote_unavailable`;
  - никогда не возвращает raw encrypted object names, access keys, crypt passwords или содержимое `rclone.conf`.
- Каноническое время хранить в UTC. CLI и веб показывают также Europe/Madrid и относительный возраст: «сегодня», «вчера», «2 дня назад».
- Не создавать открытый `catalog.json` рядом с raw S3 objects: даты, имена и структура остаются зашифрованными.

## CLI and web interfaces

- Расширить `autonomo-tax backup` (сейчас только SQLite snapshot) командами:
  - `list [--class daily|monthly] [--json]`;
  - `inspect <backup_id>`;
  - `fetch <backup_id> --out <empty-directory>`;
  - `verify <backup_id>` — скачать пару, проверить archive SHA-256 и manifest;
  - `drill <backup_id> --target-root <empty-directory>` — fetch, verify, extract, SQLite integrity/FK/schema checks.
- `verify` и `drill` используют ту же проверку, что `ops/verify-backup.sh`, и то же состояние в `ops/backup_state.py`, а не вторую реализацию. `drill` не меняет production private root и не останавливает production service.
- Production cutover оставить только в `ops/restore.sh` с явным флагом подтверждения. Веб-интерфейс не получает операцию замены production БД.
- Добавить `GET /api/backups` (существующий `GET /api/settings/backups` остаётся для настроек):
  - `generated_at`, `timezone`, состояние crypt remote;
  - latest daily/monthly;
  - последний restore drill;
  - массив backup entries с ID, типом, UTC/local временем, возрастом, размерами, schema/release и статусом пары.
- Добавить read-only раздел «Резервные копии»:
  - карточки «Последний daily», «Последний monthly», «Последняя проверка» (daily и monthly), «Шифрование»; настройки сейчас показывают только monthly-проверку;
  - таблица: дата, тип, возраст, размер, schema, release, расположение, состояние;
  - фильтры daily/monthly и ready/problematic;
  - кнопки «Обновить» и «Показать команды восстановления»;
  - без download archive через браузер и без отображения серверных путей/секретов.
- Для этого раздела скрывать period selector и кнопку «Добавить»: backups не относятся к налоговому периоду.
- Поддержать RU/EN, keyboard navigation, loading/empty/error states и мобильную таблицу-карточки.
- Доступ оставить через существующую Tailscale session и same-origin защиту. Поскольку v1 read-only, отдельная admin role не требуется.

## Disaster recovery documentation

- Runbook уже есть. После выпуска manifest v2 обновить в нём описание manifest (сейчас там нет release SHA и schema), убрать фразу об отсутствии `backup list/verify/drill` и заменить ручной выбор пары на `backup list` и `backup drill`.

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
