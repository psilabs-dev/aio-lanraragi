# aio-lanraragi Development Context

`LANraragi` is a web application for archival and reading manga/doujinshi. 
`aio-lanraragi` is an asynchronous API client for LANraragi, written in Python
with aiohttp.

The `aio-lanraragi` file structure is as follows:
```
/root
|- .github/
|- integration_tests/                               # integration testing source code
    |- src/
        |- aio_lanraragi_tests/
            |- __init__.py
            |- common.py                            # Common utilities for file signatures, checksums, and validation
            |- lrr_docker.py                        # Docker environment setup and management for LANraragi testing
            |- archive_generation/                  # Archive creation utilities for testing
                |- __init__.py
                |- archive.py                       # Main archive creation and compression logic
                |- enums.py                         # Archive compression strategy enums
                |- metadata.py                      # Metadata handling for generated archives
                |- models.py                        # Data models for archive generation requests/responses
                |- page.py                          # Individual page generation within archives
                |- utils.py                         # Utility functions for archive generation
            |- resources/                           # Test resources (fonts, sample data)
    |- tests/
        |- __init__.py
        |- conftest.py                              # Pytest configuration and fixtures
        |- test_simple.py                           # Basic integration tests
        |- ui/
            |- __init__.py
            |- test_login.py                        # Login and authentication tests
    |- README.md
    |- pyproject.toml
|- src/lanraragi/                                   # aio-lanraragi source code
    |- clients/
        |- api_clients/
            |- __init__.py
            |- archive.py                           # Archive management (upload, download, metadata, thumbnails)
            |- base.py                              # Base abstract class for all API clients
            |- category.py                          # Category management (create, update, archive assignment)
            |- database.py                          # Database operations (stats, backup, cleanup)
            |- minion.py                            # Background job monitoring
            |- misc.py                              # Server info, plugins, OPDS catalog
            |- search.py                            # Archive search and random selection
            |- shinobu.py                           # File watcher daemon control
            |- tankoubon.py                         # Tankoubon (collection) management
        |- res_processors/
            |- __init__.py
            |- archive.py                           # Response processing for archive APIs
            |- category.py                          # Response processing for category APIs
            |- database.py                          # Response processing for database APIs
            |- minion.py                            # Response processing for minion job APIs
            |- misc.py                              # Response processing for misc APIs
            |- search.py                            # Response processing for search APIs
            |- tankoubon.py                         # Response processing for tankoubon APIs
        |- __init__.py
        |- api_context.py                           # Low-level ApiContextManager for arbitrary LRR API calling
        |- client.py                                # High-level LRRClient for documented LRR API calling
        |- utils.py
    |- models/
    |- __init__.py
|- tests/                                           # aio-lanraragi unit tests (stub)
|- README.md
|- pyproject.toml
```

## Integration Testing

> All commands run in this section are within the `integration_tests` subdirectory.

A primary purpose of `aio-lanraragi` is to perform integration tests against the LANraragi codebase,
while also ensuring its API calling logic is correct. UI testing is performed with Playwright.

```
usage: pytest tests [--build] [--image] [--git-url] [--git-branch] [--docker-api] [--experimental] [--failing] [--ui]

options:
  --build BUILD                 Path to docker build context for LANraragi. Overrides the --image flag.
  --image IMAGE                 LANraragi image to use. Defaults to "difegue/lanraragi".
  --git-url GIT_URL             Link to a LANraragi git repository (e.g. fork or branch).
  --git-branch GIT_BRANCH       Branch to checkout; if not supplied, uses the main branch.
  --docker-api                  Enable docker api to build image (e.g., to see logs). Needs access to unix://var/run/docker.sock.
  --experimental                Run tests against experimental/in-development features.
  --failing                     Run tests that are known to fail.
```

