# Migrations

One file per historical migration the factory drives through SnowConvert AI (ADR 0028): the SQL Server source, the schemas that move, the archive-store schema they land in, and the command line of each phase. `astra-data migrate validate` checks every file on each pull request; `astra-data migrate run` runs the phases and writes the converted DDL, the logs and the results to `releases/<id>-migration/`.

```
migrations/
  examples/loader_sqlserver.yaml   the Loader database into ARCHIVE; the argument lists to confirm against the installed CLI
  examples/test-schema.sql         a small SQL Server schema for the end-to-end run before the real database
```

Secrets are named by environment variable in the file and never written down. The first live run follows [docs/runbooks/historical-migration.md](../docs/runbooks/historical-migration.md).
