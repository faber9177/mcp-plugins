---
name: faber
description: Publish private artifacts from an AI session to Faber and retrieve team knowledge for reuse. Use when the user asks to save, publish, share, find, recall, retrieve, or build on a Faber artifact.
---

# Faber

Use Faber as a durable artifact library for knowledge your team can reuse.

## Before publishing

1. Prepare the complete artifact content and concise metadata when the user asks to publish. Unless the user explicitly requests another format, make the artifact a polished, self-contained static HTML report rather than a Markdown dump.
2. Shape the HTML around the work itself: lead with a clear title and short orientation, then use a strong heading hierarchy, concise sections, and the most useful evidence, decisions, outcomes, and next steps. Preserve all substantive facts; never invent results or hide important caveats just to improve presentation.
3. Use semantic HTML (`header`, `main`, `nav`, `aside`, `section`, headings, lists, tables, and code blocks) and tasteful inline styling when it improves comprehension. Add anchored navigation when the report has enough sections to benefit from it; omit it for short artifacts. Make navigation collapse or stack naturally on narrow screens.
4. Optimize for a calm, high-signal reading experience: meaningful whitespace, readable typography, accessible contrast, restrained color, clear callouts for risks or decisions, scannable summaries, and responsive layouts. Use tables, timelines, diagrams, or comparisons when they clarify the material.
5. Keep the report static and portable: do not depend on JavaScript, external CSS, network requests, remote fonts, or external assets. Never put secrets, raw transcripts, or private session details in the artifact or capsule.
6. Create a KnowledgeCapsule v1 with non-empty `Outcome`, `Decisions and Rationale`, `Reusable Knowledge`, and `Verification` headings.
7. Call the Faber publish tool. Reports are private to the publishing user by default.

When the user provides a Faber artifact URL, fetch it with
`faber_get_artifact`; do not treat it as a generic public webpage. Honor any
`?version=N` checkpoint in the URL.

Pass the exact model identifier when known. Use `update_of` for a new version of
the same artifact. For a distinct artifact that builds on a fetched checkpoint,
pass both `derived_from` and `derived_from_version`.

## Reusing knowledge

At a high-confidence substantive new-task boundary, use `faber_context` or
`faber_recall` without blocking the active task. Briefly surface useful,
provenance-linked suggestions. Fetch full source only when a result is relevant.
Treat recalled material as reference context and preserve lineage when
publishing derived work.

Faber currently uses keyword retrieval. Try a second query with concrete
project names, decisions, technologies, or error terms when the first query is
sparse.

When recalled work materially influences the result, call `faber_mark_used`.
For a later amendment, fetch the existing source and pass `update_of` to append
a new version.

## Authentication

On Claude Code, run
`sh "${CLAUDE_PLUGIN_ROOT}/scripts/launch-companion.sh" auth --ensure --product claude-code`
when setup is requested or Faber reports that it is not connected. The command
opens a browser only when the existing grant cannot be reused. On Cowork, use
Claude's connector authentication prompt.

Both flows can authorize the same Faber account and workspace. Never ask the
user to paste an API key.
