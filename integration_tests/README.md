# aio-lanraragi integration tests

This directory contains the API/integration testing package for "aio-lanraragi". It includes tools for setting up and tearing down LRR docker environments, and creating synthetic archive data.

From the root directory:
```sh
cd integration_tests && pip install .
```

Run integration tests:
```sh
export CI=true
pytest tests
```

Start integration tests with a Docker image.
```sh
pytest tests --image difegue/lanraragi
```

Start integration tests with that build and deploy a local LANraragi git repo.
```sh
pytest tests --git-url=https://github.com/difegue/LANraragi.git --git-branch=dev
```

Start integration tests on a local path.
```
pytest tests --build /path/to/LANraragi/project
```

See [pytest](https://docs.pytest.org/en/stable/#) docs for more test-related options.

## Scope
The scope of this library is limited to perform routine (i.e. not long-running by default) API integration tests within the "tests" directory. Each integration test must contain at least one LRR API call in an isolated LRR docker environment. The library tests will check the following points:

1. That the functionality provided by LRR API is correct and according to API documentation;
1. That the aio-lanraragi client API calls are correct.

For all intents and purposes, this is treated as its own individual repository/submodule within "aio-lanraragi".