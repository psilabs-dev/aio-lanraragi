# aio-lanraragi integration tests

This directory contains the API/integration testing package for "aio-lanraragi". It includes tools for setting up and tearing down LRR docker environments, and creating synthetic archive data.

Integration testing version updates apply only if changes to integration testing code or tests have occurred.

For information on setting up a developer environment for testing, refer to [development](/docs/development.md). Additionally, see [resource management](/integration_tests/docs/resource_management.md) for documentation on how resources are managed during test-time.

## Usage

Integration testing relies on a deployment environment. Currently two environments (Docker, Windows runfile) are supported. Ensure port range 3010-3020 (LRR testing ports) and 6389-6399 (Redis testing ports) are available.

### Docker Deployment

Install `aio-lanraragi` from the root directory, then:
```sh
cd integration_tests && pip install .
```

Enable BuildKit (or prepend it for your tests):
```sh
export DOCKER_BUILDKIT=1
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

Run integration tests on Windows from a pre-built distribution and an available staging directory:
```sh
pytest tests --win-dist /path/to/win-dist --staging /path/to/staging
```

### Deterministic Testing

By default, random variable sampling (e.g. for tag generation or list shuffling) is induced by seed value 42 via a numpy generator. You may change the seed to something else:
```sh
pytest tests/test_auth.py --npseed 43
```

### [Playwright](https://playwright.dev/python/docs/library) UI Testing

Playwright integration tests are experimental. Install with:
```sh
pip install playwright
playwright install
pytest tests --playwright
```

Due to certain event loop quirks with `pytest-playwright` and compatibility issues with `pytest-asyncio`, we will only use `playwright`.

The process of adding UI tests can be broken to the following:
- outlining the UI steps taken
- converting those steps to Playwright

The conversion stage may be assisted via [codegen](https://playwright.dev/docs/codegen-intro), a tool which records browser actions into Python Playwright code:
```sh
npx playwright codegen --target=python http://localhost:3001
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
The scope of this library is limited to perform routine (i.e. not long-running by default) API integration or E2E tests within the "tests" directory. The library tests will confirm one or more of the following points:

1. That the LRR server deployment was successful;
1. That the functionality provided by LRR API is correct and according to API documentation;
1. That the aio-lanraragi client API calls are correct.
1. That LRR exhibits expected browser-side behavior.
