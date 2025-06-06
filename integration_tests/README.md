This directory contains the integration testing package for aio-lanraragi. It includes tools for setting up and tearing down LRR docker environments, and creating synthetic archive data.

From the root directory:
```sh
cd integration_tests && pip install .
```

Run integration tests:
```sh
export CI=true
pytest tests
```

Start integration tests with a custom image.
```sh
pytest tests --image custom-lrr-image
```

Start integration tests with that build and deploy a local LANraragi git repo.
```sh
pytest tests --build /path/to/lanraragi/project
```
