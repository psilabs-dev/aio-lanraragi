# aio-lanraragi

An asynchronous Python API client for [LANraragi](https://github.com/Difegue/LANraragi), written with [aiohttp](https://github.com/aio-libs/aiohttp) and [pydantic](https://github.com/pydantic/pydantic) for type validation. The project is a work in progress (and subject to potential breaking changes on minor version).

Installation with pip:

```sh
pip install aio-lanraragi
```

Or, to install a nightly version:

```sh
pip install git+https://github.com/psilabs-dev/aio-lanraragi.git@$branch_name
```

For development documentation, see [development](/docs/development.md). For integration testing documentation, see [integration tests README](/integration_tests/README.md).

## Usage

Here is an example on getting all archives from LANraragi using `LRRClient`'s context management:
```python
import asyncio
from lanraragi import LRRClient

async def main():
    async with LRRClient("http://localhost:3000", lrr_api_key="lanraragi") as lrr:
        response, err = await lrr.archive_api.get_all_archives()
        if err:
            raise Exception(f"Encountered error while getting all archives: {err.error}")
        for archive in response.data:
            print(archive.arcid)

if __name__ == "__main__":
    asyncio.run(main())
```
"aio-lanraragi" uses a (response, error) approach to error handling.

Here is a code snippet on making concurrent API calls under a bounded semaphore.
```python
...
async with LRRClient("http://localhost:3000", lrr_api_key="lanraragi") as lrr:
    semaphore = asyncio.BoundedSemaphore(value=4)
    tasks = []
    for req in requests:
        tasks.append(asyncio.create_task(async_task(lrr, req, semaphore)))
    await asyncio.gather(*tasks)
```

Since aiohttp is used under the hood, you may install optional libraries (namely, aiodns and brotli) for more optimization:
```sh
pip install "aiohttp[speedups]"
```

Only [officially documented APIs](https://sugoi.gitbook.io/lanraragi/api-documentation) will be supported. Undocumented API calls may be invoked at the `ApiContextManager` layer by library users. Under-development APIs shall be decorated with an `@experimental` tag in library and during testing. Deprecated APIs shall be decorated with a `@deprecated` tag.

### Session/Connector Context Cleanup

On initializing `LRRClient` or `ApiContextManager`, you may pass a selection of `aiohttp`-specific key parameters. The supported parameters currently include "ssl", "connector", and "client_session".

When "connector" or "client_session" are not provided, the context automatically creates them. These created resources are automatically cleaned up when leaving context/scope.

At the same time, if "connector" or "client_session" are provided, the context does not close them automatically.

Parameter hierarchy is determined by *containment*: the configuration which contains the other configuration takes precedence, rendering the latter ineffective. Concretely:

- if both "client_session" and "connector" are provided, then "client_session" overrides the effects of "connector", because "client_session" includes "connector".
- if both "connector" and "ssl" are provided, then "connector" takes precedence, because "connector" includes "ssl".

## Library Description
All request/response classes are under the "src/lanraragi/models" directory, and inherit the `LanraragiRequest` and `LanraragiResponse` base class, respectively.

The `ApiContextManager` is an asynchronous HTTP context manager and handle the HTTP interaction with LANraragi.

> If you don't want to use request/response DTOs, go with the API context manager.

An `ApiClient` represents a logical client for a collection of related APIs. They handle the DTO and request/response marshalling layer. These are only to be used by the LRRClient.

The `LRRClient` is a higher abstraction than `ApiContextManager` which also provides API clients for easy access in a context.

## Version Policy

"aio-lanraragi" will use major/minor/patch versions but stay on "0.x.x" for now.

Version update is necessary only if there is change to the `src/` code (comments, styling and documentation don't count). Changes to integration tests will not require version updates.

## Scope

"aio-lanraragi" is designed to abstract away the boilerplate of interfacing with a LANraragi server in Python, to allow the user to focus on application business logic, rather than the HTTP/API protocols. This includes:

- automatically choosing the REST method and parameters for the API
- conversion between request, response and Pydantic DTOs for each API call
- comfortable type hinting and rigorous request/response validation with Pydantic
- aiohttp exception retry with exponential backoff
- session management logic and implementation abstraction

The scope of this library is to implement LANraragi-related API functionality which comes naturally from supporting dependencies like aiohttp/pydantic, such as session-related functionality, or type validation. On the other hand, derivative features (features which can be implemented simply by invoking multiple APIs, sometimes with the help of additional dependencies) are *not* supported by aio-lanraragi. Derivative features include:

- get version of server
- delete all archives by a tag or pattern
- upload archives from a folder
- checking if an image is incomplete by reading file bytes

In other words, **scripting** does not fall under the library's responsibility.

Until a feasable client-server version-handling strategy is implemented, support for only the **most current** API specification falls under the scope of the project. Request/response changes across version updates, bug fixes, corrections, tests, and slight code practice alignments also fall within the scope of this project.

<!-- how to do a release for dummies

    git tag -a v0.1.4 -m "version-message"
    git push origin v0.1.4

 -->