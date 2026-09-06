"""Target profiles: the platform conventions a config is rendered for (product spec Section 4).

A profile names the table format, ingestion service, transformation engine,
orchestration, catalog, alerting and IaC of a target. The renderers of E3
are written per profile; a config names the profile it targets and the
compiler rejects one the factory has no renderers for.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TargetProfile:
    id: str
    name: str
    table_format: str
    ingestion: str
    engine: str
    orchestration: str
    catalog: str
    alerting: str
    iac: str

    def to_dict(self) -> dict:
        return asdict(self)


TARGET_PROFILES: dict[str, TargetProfile] = {
    "snowflake_iceberg": TargetProfile(
        id="snowflake_iceberg",
        name="Snowflake with managed Iceberg tables",
        table_format="Snowflake-managed Iceberg on an external volume, synced to Open Catalog",
        ingestion="Snowpipe auto-ingest from S3 events",
        engine="Snowflake SQL, Snowflake Scripting procedures, SQL UDFs",
        orchestration="Serverless tasks",
        catalog="Snowflake Open Catalog (Iceberg REST)",
        alerting="CONTROL.ALERTS routed to Slack, Jira and email",
        iac="Terraform (infra/terraform/foundation)",
    ),
}


def get_profile(profile_id: str) -> TargetProfile | None:
    return TARGET_PROFILES.get(profile_id)


def profile_ids() -> list[str]:
    return sorted(TARGET_PROFILES)
