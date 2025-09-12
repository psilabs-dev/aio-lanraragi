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
pytest tests --windows-runfile /path/to/run.ps1
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

## Resource Management during Automated Test Deployments
To prepare for potential distributed testing, we should ensure all resources provided by the test host during the lifecycle of a test session are available to one (and only one) test case. All such resources should be reclaimed at the end of tests, and at the end of a failed test or exception. Examples of resources include: networks, volumes, containers, ports, and temporary directories.

To streamline resource management, each test deployment is passed a `resource_prefix` and a `port_offset`. The former is prepended to the names of all named resources, while the latter is added to the default port values of service resources.

The following are general rules for provisioning resources:

- all automated testing resources should start with `test_` prefix.
- all LRR automated testing containers should expose ports within the range 3010-3020.
- all redis automated testing containers should expose ports within the range 6389-6399.

In a docker deployment, considered resources are as follows:

| resource | deployment type | format | description |
| - | - | - | - |
| LRR contents volume | docker | "{resource_prefix}lanraragi_contents" | name of docker volume for LRR archives storage |
| LRR thumbnail volume | docker | "{resource_prefix}lanraragi_thumb" | name of docker volume for LRR thumbnails storage |
| redis volume | docker | "{resource_prefix}redis_data" | name of docker volume for LRR database |
| network | network | "{resource_prefix}network" | name of docker network |
| LRR container | docker | "{resource_prefix}lanraragi_service" | |
| redis container | docker | "{resource_prefix}redis_service | |
| LRR image | docker | "integration_test_lanraragi:{global_id} | |
| contents directory | windows | "{resource_prefix}content" | name of the windows contents directory containing everything |

> For example: if `resource_prefix="test_lanraragi_` and `port_offset=10`, then `network=test_lanraragi_network` and the redis port equals 6389.

Since docker test deployments rely only on one image, we will pin the image ID to the global run ID instead.

## Scope
The scope of this library is limited to perform routine (i.e. not long-running by default) API integration tests within the "tests" directory. Each integration test must contain at least one LRR API call in an isolated LRR docker environment. The library tests will check the following points:

1. That the functionality provided by LRR API is correct and according to API documentation;
1. That the aio-lanraragi client API calls are correct.

For all intents and purposes, this is treated as its own individual repository/submodule within "aio-lanraragi".
