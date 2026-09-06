"""Generated tests for one source: queries that return failing rows."""

from __future__ import annotations

from astra_data.compiler import CompiledConfig
from astra_data.render.names import BRONZE, CONTROL, files_table, logical_columns, problems_table, q, record_table

PENDING_HOURS = 24


def render(compiled: CompiledConfig) -> dict[str, str]:
    spec = compiled.spec
    source = compiled.id
    files = f"{BRONZE}.{q(files_table(compiled))}"
    problems = f"{BRONZE}.{q(problems_table(compiled))}"
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
    merge_keys = tuple(k.upper() for k in spec.merge.keys) if spec.merge else ()
    for label in spec.logical_records():
        table = f"{BRONZE}.{q(record_table(compiled, label))}"
        columns = {c.name for c in logical_columns(spec, label)}
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
