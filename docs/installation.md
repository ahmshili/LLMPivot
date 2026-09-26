Part of the [LLMPivot](../README.md) documentation.

# Installation

Everything LLMPivot needs before you run `uv sync`.

## Table of contents

- [1. Python](#1-python)
- [2. uv](#2-uv)
- [3. LiteLLM](#3-litellm)
- [Verifying your setup](#verifying-your-setup)

## 1. Python

LLMPivot targets **Python 3.13+**. You generally don't need to install
Python yourself — `uv` (below) downloads and manages the right version
automatically the first time you run `uv sync`. If you'd rather install
Python directly:

| OS | Recommended install method |
|---|---|
| Linux | Your distro's package manager (e.g. `sudo apt install python3.13`), or [pyenv](https://github.com/pyenv/pyenv) for version management |
| macOS | `brew install python@3.13` via [Homebrew](https://brew.sh/), or [pyenv](https://github.com/pyenv/pyenv) |
| Windows | [python.org/downloads](https://www.python.org/downloads/), or `winget install Python.Python.3.13` |

Full official instructions: [python.org/downloads](https://www.python.org/downloads/).

## 2. uv

[`uv`](https://docs.astral.sh/uv/) is the package/dependency manager this
project uses (a faster, modern alternative to plain `pip` + `venv`).

| OS | Install command |
|---|---|
| Linux / macOS | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| macOS (Homebrew) | `brew install uv` |
| Windows (PowerShell) | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| Windows (winget) | `winget install --id=astral-sh.uv -e` |
| Any OS (via pipx) | `pipx install uv` |

Full official instructions, including other package managers and CI
usage: [docs.astral.sh/uv/getting-started/installation](https://docs.astral.sh/uv/getting-started/installation/).

After installing, restart your terminal and confirm it's on your `PATH`:

```bash
uv --version
```

## 3. LiteLLM

LLMPivot doesn't talk to provider APIs directly — it routes through a
[LiteLLM](https://github.com/BerriAI/litellm) instance, which needs to
be running separately. The recommended way to run it is via Docker,
which works identically across Linux, macOS, and Windows (with Docker
Desktop):

| OS | Docker install |
|---|---|
| Linux | [docs.docker.com/engine/install](https://docs.docker.com/engine/install/) (distro-specific) |
| macOS | [Docker Desktop for Mac](https://docs.docker.com/desktop/install/mac-install/) |
| Windows | [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) (requires WSL2) |

Once Docker is installed, see [`docs/docker.md`](docker.md) for the
exact `docker run` / `docker-compose` commands to bring up LiteLLM. If
you'd rather run LiteLLM without Docker (e.g. via `pip install litellm[proxy]`),
their own docs cover that: [docs.litellm.ai](https://docs.litellm.ai/docs/proxy/deploy).

## Verifying your setup

```bash
python3 --version   # or: py --version   (Windows)
uv --version
docker --version    # only needed if running LiteLLM via Docker
```

If all three return a version number, you're ready to follow the main
[Quickstart](../README.md#quickstart) in the README.
