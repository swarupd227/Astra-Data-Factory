"""Generated tests for one source: queries that return failing rows."""

from __future__ import annotations

from astra_data.compiler import CompiledConfig
from astra_data.render.names import BRONZE, CONTROL, EXCEPTIONS, SILVER, exceptions_table, file_metadata_table, files_table, parse_problems_table, q, record_table, silver_table

PENDING_HOURS = 24


def render(compiled: CompiledConfig) -> dict[str, str]:
    spec = compiled.spec
    source = compiled.id
    files = f"{BRONZE}.{q(files_table(compiled))}"
    problems = f"{BRONZE}.{q(parse_problems_table(compiled))}"
    tests: dict[str, str] = {}

    tests[f"tests/{source}_files_not_stuck.sql"] = "\n".join(
        [
            f"-- {source}: a registered file is processed within {PENDING_HOURS} hours. Returns files still pending after that.",
            'SELECT "FILE_NAME", "FIRST_SEEN_AT"',
            f"FROM {files}",
            f"WHERE \"STATUS\" = 'pending' AND \"FIRST_SEEN_AT\" < DATEADD('hour', -{PENDING_HOURS}, SYSDATE());",
            "",
        ]
    )
    tests[f"tests/{source}_files_registered_once.sql"] = "\n".join(
        [
            f"-- {source}: a landed file is registered once. Returns file names registered more than once.",
            'SELECT "FILE_NAME", COUNT(*) AS ROW_COUNT',
            f"FROM {files}",
            'GROUP BY "FILE_NAME"',
            "HAVING COUNT(*) > 1;",
            "",
        ]
    )
    tests[f"tests/{source}_problem_codes_known.sql"] = "\n".join(
        [
            f"-- {source}: every problem carries a rejection code from the taxonomy. Returns codes CONTROL.REJECTION_CODES does not have.",
            'SELECT p."CODE", COUNT(*) AS ROW_COUNT',
            f"FROM {problems} p",
            f'LEFT JOIN {CONTROL}."REJECTION_CODES" r ON r."CODE" = p."CODE"',
            'WHERE r."CODE" IS NULL',
            'GROUP BY p."CODE";',
            "",
        ]
    )
    tests[f"tests/{source}_files_parse_complete.sql"] = "\n".join(
        [
            f"-- {source}: every parsed file has its header and trailer and no excluded rows. Returns files with file-level problems or excluded rows.",
            'SELECT "FILE_NAME", "FILE_PROBLEMS", "EXCLUDED_ROWS"',
            f"FROM {BRONZE}.{q(file_metadata_table(compiled))}",
            'WHERE "FILE_PROBLEMS" > 0 OR "EXCLUDED_ROWS" > 0;',
            "",
        ]
    )
    merge_keys = tuple(k.upper() for k in spec.merge.keys) if spec.merge else ()
    if spec.merge:
        scope = [f"SCOPE_{s.upper()}" for s in spec.merge.scope]
        key_list = ", ".join(q(k) for k in list(scope) + list(merge_keys))
        tests[f"tests/{source}_silver_active_keys_unique.sql"] = "\n".join(
            [
                f"-- {source}: one active Silver row per scope and key ({', '.join(spec.merge.keys)}). Returns keys with more than one.",
                f"SELECT {key_list}, COUNT(*) AS ROW_COUNT",
                f"FROM {SILVER}.{q(silver_table(compiled))}",
                'WHERE "RETIRED_AT" IS NULL',
                f"GROUP BY {key_list}",
                "HAVING COUNT(*) > 1;",
                "",
            ]
        )
        tests[f"tests/{source}_exception_codes_known.sql"] = "\n".join(
            [
                f"-- {source}: every exception carries a rejection code from the taxonomy. Returns codes CONTROL.REJECTION_CODES does not have.",
                'SELECT e."REJECTION_CODE", COUNT(*) AS ROW_COUNT',
                f"FROM {EXCEPTIONS}.{q(exceptions_table(compiled))} e",
                f'LEFT JOIN {CONTROL}."REJECTION_CODES" r ON r."CODE" = e."REJECTION_CODE"',
                'WHERE r."CODE" IS NULL',
                'GROUP BY e."REJECTION_CODE";',
                "",
            ]
        )
        tests[f"tests/{source}_files_merged_once_logged.sql"] = "\n".join(
            [
                f"-- {source}: every merged file has exactly one merge log entry. Returns merged files with none or several.",
                'SELECT f."FILE_NAME", COUNT(m."FILE_NAME") AS LOG_ENTRIES',
                f"FROM {BRONZE}.{q(files_table(compiled))} f",
                f"LEFT JOIN {CONTROL}.\"MERGE_LOG\" m ON m.\"FILE_NAME\" = f.\"FILE_NAME\" AND m.\"SOURCE_ID\" = '{source}'",
                "WHERE f.\"STATUS\" = 'merged'",
                'GROUP BY f."FILE_NAME"',
                "HAVING COUNT(m.\"FILE_NAME\") <> 1;",
                "",
            ]
        )
    for record in spec.records:
        if record.type != "detail":
            continue
        label = record.label
        table = f"{BRONZE}.{q(record_table(compiled, label))}"
        columns = {f.name.upper() for f in record.fields}
        keys = merge_keys if merge_keys and all(k in columns for k in merge_keys) else ()
        if keys:
            key_list = ", ".join(q(k) for k in keys)
            tests[f"tests/{source}_{label}_keys_unique_per_file.sql"] = "\n".join(
                [
                    f"-- {source} {label}: within one file the merge keys ({', '.join(keys)}) identify one record. Returns duplicates.",
                    f'SELECT "FILE_NAME", {key_list}, COUNT(*) AS ROW_COUNT',
                    f"FROM {table}",
                    f'GROUP BY "FILE_NAME", {key_list}',
                    "HAVING COUNT(*) > 1;",
                    "",
                ]
            )
        tests[f"tests/{source}_{label}_lines_traceable.sql"] = "\n".join(
            [
                f"-- {source} {label}: every parsed record names a registered file. Returns records whose file is not in {files_table(compiled)}.",
                'SELECT r."FILE_NAME", COUNT(*) AS ROW_COUNT',
                f"FROM {table} r",
                f'LEFT JOIN {files} f ON f."FILE_NAME" = r."FILE_NAME"',
                'WHERE f."FILE_NAME" IS NULL',
                'GROUP BY r."FILE_NAME";',
                "",
            ]
        )
    return tests
