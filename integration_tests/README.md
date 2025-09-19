# aio-lanraragi integration tests

This directory contains the API/integration testing package for "aio-lanraragi". It includes tools for setting up and tearing down LRR docker environments, and creating synthetic archive data.

Versioning of `integration_tests` is synced to that of `aio-lanraragi`.

For information on setting up a developer environment for testing, refer to [development](/docs/development.md).

## Usage

Integration testing relies on a deployment environment. Currently two environments (Docker, Windows runfile) are supported. Ensure port range 3010-3020 (LRR testing ports) and 6389-6399 (Redis testing ports) are available.

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

Run integration tests on Windows from a pre-built distribution and an available staging directory:
```sh
pytest tests --win-dist /path/to/win-dist --staging /path/to/staging
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

### Test-time Resource Management
To prepare for potential distributed testing, we should ensure all resources provided by the test host during the lifecycle of a test session are available to one (and only one) test case. All such resources should be reclaimed at the end of tests, and at the end of a failed test or exception, *provided* they were produced during test-time. Examples of resources include: networks, volumes, containers, ports, build artifacts, processes, files, and directories.

To streamline resource management, each test deployment is passed a `resource_prefix` and a `port_offset`. The former is prepended to the names of all named resources, while the latter is added to the default port values of service resources.

On Windows hosts, multiple deployments are achieved by having multiple copies of the same distribution, as LRR processes are identified by their perl executable path. To this end, we must provide a staging directory that stores not only these distributions, but also all persistent, writable and isolated data. The staging directory and the win-dist directory should not overlap. Multiple tests may operate within the staging directory concurrently, provided they work in their own respective resource prefix namespaces and port offsets. The staging directory should not contain data unrelated to tests, or children without a resource prefix. The staging directory should be same across all tests. On Github workflows, for example, all tests live under the staging directory `$env:GITHUB_WORKSPACE\staging`.

The following are general rules for provisioning resources. Likewise, the user must ensure that their environment provides these resources for testing:

- all automated testing resources should start with `test_` prefix.
- all LRR automated testing containers should expose ports within the range 3010-3020.
- all redis automated testing containers should expose ports within the range 6389-6399.
- when testing on Windows (or on the host machine in general): all files should be written within a resource-prefixed directory within the staging directory.

In a test deployment, considered resources are as follows:

| resource | deployment type | format | description |
| - | - | - | - |
| LRR contents volume | docker | "{resource_prefix}lanraragi_contents" | name of docker volume for LRR archives storage |
| LRR thumbnail volume | docker | "{resource_prefix}lanraragi_thumb" | name of docker volume for LRR thumbnails storage |
| redis volume | docker | "{resource_prefix}redis_data" | name of docker volume for LRR database |
| network | network | "{resource_prefix}network" | name of docker network |
| LRR container | docker | "{resource_prefix}lanraragi_service" | |
| redis container | docker | "{resource_prefix}redis_service | |
| LRR image | docker | "integration_test_lanraragi:{global_id} | |
| windist directory | windows | "{resource_prefix}win-dist" | removable copy of the Windows distribution of LRR in staging |
| contents directory | windows | "{resource_prefix}contents" | contents directory of LRR application in staging |
| temp directory | windows | "{resource_prefix}temp" | temp directory of LRR application in staging |
| redis | windows | "{resource_prefix}redis" | redis directory in staging |
| log | windows | "{resource_prefix}log" | logs directory in staging |
| pid | windows | "{resource_prefix}pid" | PID directory in staging |

> For example: if `resource_prefix="test_lanraragi_` and `port_offset=10`, then `network=test_lanraragi_network` and the redis port equals 6389.

Since docker test deployments rely only on one image, we will pin the image ID to the global run ID instead.

## Scope
The scope of this library is limited to perform routine (i.e. not long-running by default) API integration tests within the "tests" directory. Each integration test must contain at least one LRR API call in an isolated LRR docker environment. The library tests will check the following points:

1. That the functionality provided by LRR API is correct and according to API documentation;
1. That the aio-lanraragi client API calls are correct.

For all intents and purposes, this is treated as its own individual repository/submodule within "aio-lanraragi".
