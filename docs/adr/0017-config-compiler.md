# ADR 0017: The config compiler resolves every reference before anything is rendered

Date: 2026-09-06
Status: Accepted
Story: S3.1.1 Validate a config against the schema (E3, F3.1, WBS 2.3.1)

## Context

A config is one versioned instance of a Source Spec mapped to the canonical model under a Target Profile, with rules and resolution parameters. Until now it was validated for shape and for the existence of what it names. Renderers need more: the spec version as an object, the pattern that parses it, the canonical column each mapping lands in, the source field it comes from, the transform between them, and the rule that governs it, with types that agree. A renderer that looks names up itself would fail late and differently in each renderer.

## Decision

1. **Compilation is a separate step from validation and produces an intermediate model.** `astra_data.compiler.compile_config` validates first (schema, spec registry, rule catalog), then resolves every reference to the object it names and every mapping to entity, column, source field, transform and rule. The result, `CompiledConfig`, holds objects, not names; `to_dict` is its serialisable form. Renderers (S3.1.2, S3.2.x) take a compiled config and nothing else.

2. **Every unknown reference is an error that names what is unknown and what would be known.** A pattern that is not in the library or does not handle the spec's format, a target profile the factory has no renderers for, a domain pack that is not under `domains/`, an entity or column that is not in the pack's latest model, a source field that is not in the spec's logical record, a transform the renderers do not know or called with the wrong arguments, and a rule that is not in the catalog or was rejected, each fail with the alternatives listed. A missing required field is reported by the schema with its name and the parent's line.

3. **Configs name their pattern and profile explicitly.** `pattern` is optional; when absent the first library pattern that handles the spec is used and recorded in the provenance. `target_profile` must be one of `astra_data.targets.TARGET_PROFILES`, which is the list of profiles that have renderers (Snowflake with managed Iceberg tables today). `domain_pack` must be a pack directory, and its latest model is the target.

4. **Transforms are a fixed vocabulary with types.** `astra_data.transforms` defines the transforms a mapping may use, their arguments and the source types they accept and yield. The compiler checks the source field's type against the transform and the result against the target column, so a date cannot be mapped into a string column, or a text field into a decimal, without a transform that makes it so. The renderers implement exactly this vocabulary.

5. **Mappings address logical records.** A mapping's source is a field of one of the spec's logical records (a paired record, a split part or a detail record); when the spec has more than one, the mapping says which with `record`. Source field names are the spec's names.

6. **Provenance is part of the model.** The compiled config records the config, spec and model files with their digests, the pattern and profile ids, and each rule with its status and last change. S3.1.2 writes it as PROVENANCE.json into the release bundle.

## Consequences

- `astra-data compile` is the command the pipeline runs after validation; CI compiles every config so a config that validates but cannot be compiled fails the pull request.
- The example config names its pattern, targets the `custodial` pack and uses the spec's field names.
- A second target profile is a new entry in `TARGET_PROFILES` plus its renderers; a config for it compiles the same way.
- Resolution parameters and DQ rules pass through the compiled model unchanged until their own stories (S3.2.4, S3.3.x) give them shape.
