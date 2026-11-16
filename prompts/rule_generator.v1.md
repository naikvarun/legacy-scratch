You are a data migration assistant. Generate a transformation rule for this APPROVED
column mapping. The rule's logic must be a single Snowflake SQL expression in which the
identifier `src` stands for the source column value.

Mapping (JSON):
{mapping}

Source column profile (JSON):
{column_profile}

Return a JSON object with: logic, null_handling, edge_cases (list of strings), confidence
(0.0-1.0). Cover every observed sample value, including dirty ones (case, whitespace,
legacy literals). If any value cannot be mapped with certainty, map it to NULL, say so in
edge_cases and lower confidence.
