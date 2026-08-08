# Faber for Codex

Faber turns useful work from Codex into durable, private artifacts that your
team can find and reuse later. This plugin works in Codex CLI and Codex
Desktop.

## Install

```bash
codex plugin marketplace add getfaber/mcp-plugins
codex plugin add faber-codex@faber-mcp-plugins
```

Faber supports macOS and Linux on Intel/AMD and Arm processors. The plugin
includes the matching native companion and does not download another executable
at runtime.

## Connect

Use a Faber capability after installation. Codex opens a browser when it needs
you to authorize access to your Faber account. You do not need to create or
paste an API key.

Faber stores credentials in the operating-system credential store and keeps
queued work in Codex's plugin data directory. Normal plugin upgrades preserve
both.

## Update

```bash
codex plugin marketplace upgrade faber-mcp-plugins
codex plugin add faber-codex@faber-mcp-plugins
```

Start a new task after installation or an update so Codex loads the current
skills and tools.

Learn more at [getfaber.app](https://www.getfaber.app).
