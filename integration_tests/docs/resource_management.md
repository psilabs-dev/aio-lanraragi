# Test-time Resource Management

To prepare for testing, we should ensure all resources provided by the test host during the lifecycle of a test session are available to one (and only one) test case.

All such resources should be reclaimed at the end of tests, and at the end of a failed test or exception, *provided* they were produced during test-time. Examples of resources include: networks, volumes, containers, ports, build artifacts, processes, files, and directories.

To streamline resource management, each test deployment is passed a `resource_prefix` and a `port_offset`. The former is prepended to the names of all named resources, while the latter is added to the default port values of service resources.

The following are general rules for provisioning resources. Likewise, the user must ensure that their environment provides these resources for testing:

- an existing staging directory.
- all automated testing resources should start with `test_` prefix.
- all LRR automated testing containers should expose ports within the range 3010-3020.
- all redis automated testing containers should expose ports within the range 6389-6399.

In a test deployment, considered resources are as follows:

| resource | deployment type | format | description |
| - | - | - | - |
| LRR archives | docker/windows | "{resource_prefix}archives" | LRR archives directory in staging |
| LRR thumbnails | docker/windows | "{resource_prefix}thumb" | LRR thumbnails directory in staging |
| LRR logs | docker/windows | "{resource_prefix}log" | LRR logs directory in staging |
| redis volume | docker | "{resource_prefix}redis_data" | name of docker volume for LRR database |
| network | network | "{resource_prefix}network" | name of docker network |
| LRR container | docker | "{resource_prefix}lanraragi_service" | |
| redis container | docker | "{resource_prefix}redis_service | |
| LRR image | docker | "integration_test_lanraragi:{global_id} | |
| windist directory | windows | "{resource_prefix}win-dist" | removable copy of the Windows distribution of LRR in staging |
| temp directory | windows | "{resource_prefix}temp" | temp directory of LRR application in staging |
| redis | windows | "{resource_prefix}redis" | redis directory in staging |
| pid | windows | "{resource_prefix}pid" | PID directory in staging |

> For example: if `resource_prefix="test_lanraragi_` and `port_offset=10`, then `network=test_lanraragi_network` and the redis port equals 6389.

Since docker test deployments rely only on one image, we will pin the image ID to the global run ID instead.