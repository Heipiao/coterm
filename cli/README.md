# Coterm CLI

Coterm CLI is the local runtime for Coterm. It creates a session on a Coterm Hub, prints a pairing QR code, and runs the agent in your local terminal environment.

This package is designed to ship as an independent product:

- publish the source on GitHub
- distribute the CLI on PyPI with `pip install coterm`
- let users override the Hub with environment variables while keeping a built-in default

Source repository:

- `https://github.com/Heipiao/coterm`

## License

This project is licensed under the GNU Affero General Public License v3.0 or later.

If you distribute a modified version of Coterm CLI, you must provide the corresponding source code under the AGPL as well.

AGPL is stricter than GPL for server software: if someone modifies Coterm CLI and lets users interact with that modified version over a network, they must also offer the corresponding source code for that running version.

## Requirements

- Python 3.10 or newer
- A reachable Coterm Hub
- Claude installed and available on `PATH` for the current alpha implementation

## Hub Configuration

Coterm CLI has a built-in default Hub:

```text
http://127.0.0.1:18083
```

You can override it with environment variables:

```bash
export COTERM_HUB=http://your-hub.example.com
```

Coterm CLI also accepts `COTERM_HUB_BASE_URL`.

Hub resolution order is:

1. `--hub`
2. `COTERM_HUB`
3. `COTERM_HUB_BASE_URL`
4. saved config in `~/.coterm/config.json`
5. built-in default `http://127.0.0.1:18083`

## Install

Install from a published package:

```bash
pip install coterm
```

Install from a cloned repository:

```bash
cd coterm/cli
pip install .
```

## Quick Start

Check prerequisites:

```bash
coterm doctor
```

Point the CLI at your Hub if you are not using the default:

```bash
export COTERM_HUB=http://your-hub.example.com
```

Save Hub credentials if your Hub requires them:

```bash
coterm auth login --hub http://127.0.0.1:18083
```

Start a session:

```bash
coterm
```

This will:

1. Create a session on the configured Hub
2. Create a pairing token
3. Print a QR code and pairing code
4. Start the local agent runtime

On the mobile side, the user scans the QR code or enters the pairing code to bind the iPhone to that session.

## Commands

```bash
coterm
coterm doctor
coterm version
coterm auth status
coterm auth login --hub http://127.0.0.1:18083
coterm hub status
```

## Configuration

Coterm CLI reads configuration from CLI flags, environment variables, and `~/.coterm/config.json`.

Important environment variables:

- `COTERM_HUB` or `COTERM_HUB_BASE_URL`: Hub base URL
- `COTERM_AUTH_TOKEN`: Hub auth token
- `COTERM_DEVICE_ID`: Override the generated local device id
- `COTERM_HOME`: Override config directory, default `~/.coterm`
- `COTERM_CLAUDE_BIN`: Path to the `claude` executable
- `COTERM_WORKING_DIR`: Default working directory

Resolution order for Hub configuration is:

1. CLI argument
2. Environment variable
3. Saved config
4. Built-in default `http://127.0.0.1:18083`

## Product Notes

This package is designed to be independently installable. However, today it still depends on external runtime prerequisites:

- A Hub must already exist or be separately installable
- Claude must be installed separately
- Authentication setup depends on your Hub deployment model

For production distribution, treat `coterm-cli` and `coterm-hub` as separate deliverables unless you intentionally publish both.

At the moment:

- `coterm` is the user-facing runtime package
- `coterm-hub` should be installed separately if you want local Hub management
- `coterm hub start` should only be documented as supported when `coterm-hub` is actually installed
