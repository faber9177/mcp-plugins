# Faber plugins for Claude

Faber turns useful work from Claude into durable, private artifacts that your
team can find and reuse later.

This repository contains two Faber plugins:

- `faber-claude-code` bundles the native Faber companion, lifecycle hooks,
  knowledge capture, encrypted offline queueing, and hosted Faber access.
- `faber-cowork` adds Faber publishing and knowledge recall to Claude Cowork.

## Install on Claude Code

Add the Faber marketplace and install the Claude Code plugin:

```bash
claude plugin marketplace add faber9177/mcp-plugins
claude plugin install faber-claude-code@faber-mcp-plugins
```

Faber for Claude Code supports macOS and Linux on Intel/AMD and Arm processors.

## Install on Claude Cowork

Install **Faber for Cowork** from Claude's plugin browser when it is available
through your organization or Anthropic's plugin directory. Organization
administrators can distribute Faber through a managed plugin marketplace.

## Connect to Faber

The first time you use Faber, Claude opens a browser so you can sign in and
authorize access to your Faber account. You do not need to create or paste an
API key.

Claude Code and Cowork maintain their own secure connections, but both can use
the same Faber account and workspace. Normal plugin updates preserve your
connection and queued work.

## What you can do

- Publish polished reports and reusable work as private Faber artifacts.
- Find relevant knowledge from artifacts you have permission to view.
- Build on earlier work while preserving its source and version lineage.
- Share durable results across Claude Code, Cowork, and the rest of your team.

Learn more at [getfaber.app](https://www.getfaber.app).
