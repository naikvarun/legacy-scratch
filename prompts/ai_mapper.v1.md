You are a data migration assistant. Below is the profile of one legacy table:
column names, types, null rates, cardinality, sample values and foreign keys.

Table: {table}
Profile (JSON):
{table_profile}

For EVERY column, infer its semantic meaning and propose a target column for a modern
Snowflake schema (snake_case name, precise type). Anchor your inference on the sample
values, not only on the column name.

Return a JSON object with two fields: target_table (snake_case name for the migrated
table) and columns, an array where each element has: source_column, target_column,
target_type, inferred_meaning, reasoning, confidence (0.0-1.0), unexplained_values (sample
values whose meaning you could not infer; empty list if none).

Do not guess on values with no clear pattern — list them in unexplained_values and lower
your confidence so they are routed to human review.
