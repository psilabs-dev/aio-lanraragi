import importlib.metadata

def get_version() -> str:
    return importlib.metadata.version("aio-lanraragi-integration-tests")
