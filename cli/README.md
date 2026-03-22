# Coterm CLI

Coterm CLI is the local runtime for Coterm. This package contains the CLI only. It starts an agent on your machine, connects it to a Coterm Hub, and prints a QR code plus pairing code so a mobile client can attach to the session.

## Install

Install from PyPI:

```bash
pip install coterm
```

If your environment already has older async dependencies installed, prefer forcing the CLI runtime requirements explicitly:

```bash
python -m pip install -U "coterm" "anyio>=4,<5"
```

This package does not install the Hub server. You need a reachable Coterm Hub, whether it is managed for you or self-hosted separately.

## Requirements

- Python 3.10 or newer
- A reachable Coterm Hub
- Claude installed and available on `PATH` for the current alpha implementation
- `anyio>=4,<5` for `claude-agent-sdk` subprocess transport compatibility

## Quick Start

Check local prerequisites:

```bash
coterm doctor
```

If you are using your own Hub, point the CLI at it explicitly:

```bash
export COTERM_HUB=http://your-hub.example.com
```

If your Hub requires authentication, save the Hub URL and token:

```bash
coterm auth login --hub http://your-hub.example.com
```

Start a session:

```bash
coterm
```

This will:

1. Contact the configured Hub
2. Request a pairing flow
3. Print a QR code and pairing code
4. Wait for a mobile client to complete pairing
5. Start the local agent runtime

Some distributions may ship with a built-in default Hub so `pip install coterm` can work out of the box. If you run your own Hub, use `--hub` or `COTERM_HUB` to override it.

## Hub Configuration

Coterm CLI resolves the Hub in this order:

1. `--hub`
2. `COTERM_HUB`
3. `COTERM_HUB_BASE_URL`
4. saved config in `~/.coterm/config.json`
5. package default, if the distribution defines one

Save Hub credentials locally:

```bash
coterm auth login --hub http://your-hub.example.com
```

Check saved configuration:

```bash
coterm auth status
```

## Commands

```bash
coterm
coterm doctor
coterm version
coterm auth status
coterm auth login --hub http://your-hub.example.com
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

## Notes

- `pip install coterm` installs the CLI package only.
- The current alpha implementation supports Claude only.
- A Hub must already exist or be installed separately.
- Authentication behavior depends on your Hub deployment model.
- If `claude-agent-sdk` fails with `open_process() got an unexpected keyword argument 'user'`, your environment is using `anyio<4`; upgrade to `anyio>=4,<5`.

## License

This project is licensed under the GNU Affero General Public License v3.0 or later.
