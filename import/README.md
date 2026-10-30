# Каталог переноса базы 1.0.x

Положите сюда файлы базы прежней версии (`puls.db` и, если есть, `puls.db-wal`, `puls.db-shm`)
и выполните из корня установки:

```bash
docker compose --profile migrate run --rm legacy-import
```

Отчёт о переносе и сверке появится здесь же: `import-report.md`. Подробно —
`docs/runbooks/v0.2.0-migration-from-1.0.1.md`.
