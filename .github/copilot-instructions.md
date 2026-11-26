# Pull request reviews

During code review, use only the checks listed below. Comment only when the diff provides concrete evidence that a check is violated. Point to the affected line and explain the impact. Skip praise, style preferences, speculative concerns, and findings already raised. If no listed check applies, leave no inline comments.

## Review checks

**Function comments and docstrings.** State what the function does and why it is needed. One sentence is enough when it covers both; keep the summary to three sentences at most. A separate brief note is optional for non-obvious behavior, such as required call ordering or an unexpected side effect. Do not ask for a comment on a self-explanatory function or flag the absence of an optional note.
