# aio-lanraragi integration tests

This directory contains the API/integration testing package for "aio-lanraragi". It includes tools for setting up and tearing down LRR docker environments, and creating synthetic archive data.

Versioning of `integration_tests` is synced to that of `aio-lanraragi`.

## Usage

Integration testing relies on a deployment environment. Currently two environments (Docker, Windows runfile) are supported.

### Docker Deployment

Install `aio-lanraragi` from the root directory, then:
```sh
cd integration_tests && pip install .
```

> All of the following are run within `aio-lanraragi/integration_tests/`.

Run integration tests on the official Docker image ("difegue/lanraragi"):
```sh
pytest tests
```

Run integration tests with a custom Docker image:
```sh
pytest tests --image myusername/customimage
```

Run integration tests with a Docker image built off a LANraragi git repo (with a custom branch if specified):
```sh
pytest tests --git-url=https://github.com/difegue/LANraragi.git --git-branch=dev
```

Run integration tests with a Docker image built off a path to a local LANraragi project:
```sh
pytest tests --build /path/to/LANraragi/project
```

### Windows Deployment

Run integration tests on Windows with a pre-built runfile:
```sh
pytest tests --windows-runfile /path/to/run.ps1 --windows-content-path /path/to/content
```

### Deterministic Testing

By default, random variable sampling (e.g. for tag generation or list shuffling) is induced by seed value 42 via a numpy generator. You may change the seed to something else:
```sh
pytest tests/test_auth.py --npseed 43
```

### Logging

To see LRR process logs accompanying a test failure, use the pytest flag `--log-cli-level=ERROR`:
```sh
pytest tests/test_simple.py::test_should_fail --log-cli-level=ERROR
# ------------------------------------------------------- live log call --------------------------------------------------------
# ERROR    tests.conftest:conftest.py:84 Test failed: tests/test_simple.py::test_should_fail
# ERROR    aio_lanraragi_tests.lrr_docker:conftest.py:96 LRR: s6-rc: info: service s6rc-oneshot-runner: starting
# ERROR    aio_lanraragi_tests.lrr_docker:conftest.py:96 LRR: s6-rc: info: service s6rc-oneshot-runner successfully started
# ERROR    aio_lanraragi_tests.lrr_docker:conftest.py:96 LRR: s6-rc: info: service fix-attrs: starting
```

On test failures, pytest will attempt to collect the service logs from the running LRR process/container before cleaning the environment for the next test.

See [pytest](https://docs.pytest.org/en/stable/#) docs for more test-related options.

## Scope
The scope of this library is limited to perform routine (i.e. not long-running by default) API integration tests within the "tests" directory. Each integration test must contain at least one LRR API call in an isolated LRR docker environment. The library tests will check the following points:

1. That the functionality provided by LRR API is correct and according to API documentation;
1. That the aio-lanraragi client API calls are correct.

For all intents and purposes, this is treated as its own individual repository/submodule within "aio-lanraragi".
