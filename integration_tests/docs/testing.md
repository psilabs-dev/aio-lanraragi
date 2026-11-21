# Testing

Before LRR had integration tests, behavioral testing/code quality was done in an ad hoc basis, often *after* the code has been merged, and code quality assurance was achieved mainly through Perl unit testing.

The motivation for `aio-lanraragi`, besides providing a typed, async Python scripting platform, is to provide CI testing.

Test coverage should never be used as a substitute for code quality and code review. For example, it cannot prevent new feature bugs, test evaluation and operational errors, or errors in system design.

## Testing Design

We refer to a live integration test as one involving at least one LRR environment and client fixture.

`aio-lanraragi` prioritizes behavioral verification, it does not attempt to produce a balanced test pyramid, nor does it subscribe to the model. It handles integration tests primarily at three test interfaces:

- Internal: test design involves direct interaction with internal state, such as LRR redis database or file system.
- API: (OpenAPI) API calls are made to simulate/test user or client behavior. AKA subcutaneous tests.
- Playwright: Playwright calls are made to simulate/test user behavior at the browser level.

For majority of test cases, API and Playwright tests are preferred, for the following reasons:

- Black box testing: test design should not know anything about LRR internal implementation and state.
- Ordinary path reproducibility: test design should not assume actions that ordinary users cannot perform in a typical environment.

A test being Playwright does not necessarily exclude the design from making API calls, provided the logic is reliable. More generally, how a test environment is set up is more flexible, compared to simulating the actual intended behavior.

Exceptions may apply for lower-level preference, for one of the following reasons:

- Performance: adding thousands of archives worth of data is faster through direct database operation than through the API.
    > This naturally comes with the risk of implementation and schema drift; hence, consider whether "thousands of archives" should be a topic of integration test in the first place.
- Availability: an internal method is preferred in the event an API method is not available or whose alternative involves Playwright and unwanted complexity.
    > Same risks apply
- Redundancy: an internal method is used as an observation interface in addition to a higher level, available interface for additional verification of expected behavior.

`aio-lanraragi` only tests documented functionality (and on platforms) that is officially supported by LRR. This includes:

- [LRR OpenAPI](https://sugoi.gitbook.io/lanraragi/dev/api-documentation/getting-started)
- LRR GUI
- OS resources (files/folders) which LRR requires and assumes owner read/write access

### Portfolio Optimization

Test cases balance between batch test coverage, scenario length, state dependencies and isolation: we can run more test cases together, avoid cost in state and behavioral isolation, while ensuring coherent scenario and desirable state dependencies.

The average test case takes around 10-20 seconds to run, this is objectively longer than any typical assertion LRR will ever encounter (in the ~0.1s range). If every bug, regression, and security problem were to introduce its own dedicated test case, our test suite would eventually run for hours per PR.

This is an optimization problem: pack state dependencies and thematically related assertions to one case, while avoiding contaminating scenarios by splitting to multiple cases, so that all dependencies are meaningful, intentional, and support case objective.

```python
# packing by theme
upload_archive()
assert total_archives() == 1 # virtually free assertion cost
delete_archive()
assert total_archives() == 0 # virtually free assertion cost

# packing by independence (valid, but thematically undesirable and risks long-scenario; split to two cases)
upload_archive()
assert total_archives() == 1
add_category()
assert total_categories() == 1

# undesired contamination (invalid testing design)
queue_archive_scan()
assert workers() == 1
assert total_archives() == 1
```

We stratify test suite into three groups:

- Basic functionality group: tests that should be run to ensure basic LRR functionality
- High coverage group: tests that should be run to ensure extensive LRR functionality
- Performance: tests that evaluate LRR performance at scale

Basic functionality tests should run before a PR is merged. High coverage tests and benchmarks should run independently of any PR, against the default or release branch, to establish acceptance.

## Test Selection

Governs criteria for when a test case should be admitted to the test suite, and under which strata to admit. Basic functionality test cases are considered only.

A basic functionality test case must answer the following questions:

- What feature, operation (lifecycle), component, or requirement is covered by this test case?
    - Examples: archive CRUD, category CRUD, authentication
- Why should this test case *not* be absorbed into any of the existing test cases?
    - Example (why archive read not in archive CRUD): "Archive reads exercise extensive functionality beyond basic archive CRUD, are largely independent of archive entity presence (don't need 100 archives, for example), involve writes to archive metadata that archive upload lifecycles do not touch. Merging both test cases introduces unwanted state, behavior contamination, as their assertions have no related state, as well as long scenarios that weaken either diagnostics."

If a test case is a regression:

- What feature requirement has the regression violated, and why do existing tests lack this requirement's coverage?

A regression test should either be generalized to strengthen an existing functionality test, or eliminated from selection, to optimize coverage per line.
