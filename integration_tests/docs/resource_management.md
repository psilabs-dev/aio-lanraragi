# Test-time Resource Management

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