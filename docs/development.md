# Development

This document covers setting up a development environment and testing basics.

## Topics

1. [Prerequisites](#prerequisites)
1. [Environment Setup](#environment-setup)
    1. [With virtualenv](#with-virtualenv)
    1. [With uv](#with-uv)
    1. [Visual Studio Code](#visual-studio-code)
1. [Unit Testing](#unit-testing)
1. [Integration Testing](#integration-testing)

## Prerequisites

An installation of Python. My recommendation is to develop with a tool which supports environment isolation; some good choices include [virtualenv](https://virtualenv.pypa.io/en/latest/), [pyenv](https://github.com/pyenv/pyenv), [uv](https://docs.astral.sh/uv/), or [conda](https://anaconda.org/anaconda/conda); use what you're comfortable with.

Docker is required only if you plan to run integration tests with the Docker deployment. In this case, a Docker Python client will interface with the API to perform container actions for integration testing.

## Environment Setup

Add developer dependencies (includes ruff, tox, pytest and so on), and install "aio-lanraragi" in editable mode:

```sh
pip install -e ".[dev]"
```

### With virtualenv
Install and test with `virtualenv` (Linux/MacOS):
```sh
virtualenv venv                 # create virtual environment at "venv"
source venv/bin/activate
pip install -e ".[dev]"
pytest tests/                   # run unit tests
```

Install with `virtualenv` (Windows):
```sh
virtualenv venv                 # create virtual environment at "venv"
.\venv\Scripts\activate
pip install -e ".[dev]"
pytest tests/                   # run unit tests
```

### With uv
Install in a virtual environment with `uv`:
```sh
uv venv                         # create virtual environment at ".venv"
uv pip install -e ".[dev]"
uv run pytest tests/            # run unit tests
```

### Visual Studio Code
The following advice applies to VS Code as well as its forks (Cursor, Codium, etc.).

You might want to install the usual VSCode Python extensions like Pylance, as well as [ruff](https://docs.astral.sh/ruff/), then apply any of these settings as you wish to `.vscode/settings.json`:

```json
{
    "python.analysis.autoImportCompletions": true,
    "python.analysis.autoFormatStrings": true,
    "python.analysis.extraPaths": [
        "integration_tests"
    ]
}
```

Additionally, VSCode supports workspaces, so it helps to create a workspace which includes both the LANraragi source code, as well as aio-lanraragi.

## Architecture

Any request or response from `LRRClient` API calls must inherit the `LanraragiRequest` and `LanraragiResponse` class, respectively.

## Unit Testing

Testing is mainly done with `pytest`, though you can also use `tox` to test multiple Python versions.

```sh
pytest tests/                   # if you're using virtualenv
uv run pytest tests/            # if you're using uv
```

Note that when running `uv`, your current working directory matters, and the above command must be run at project root.

Resources should be carefully managed, and unclosed client sessions are a hard error, as configured into pytest in unit and integration tests.

## Integration Testing

Integration testing is an important part of this client library, and it has its own supporting library. To install dependencies, go to `integraion_tests` and install them (to your environment):

```sh
cd integration_tests
pip install -e .
pytest tests
```

or if you are using `uv`, run:

```sh
uv pip install -e ./integration_tests   # IMPORTANT: include "./"
uv run pytest integration_tests/tests
```

For more info on integration testing, see [integration_tests README](/integration_tests/README.md).
