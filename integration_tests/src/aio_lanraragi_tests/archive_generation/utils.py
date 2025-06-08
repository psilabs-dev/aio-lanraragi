import importlib.resources

def get_roberta_regular_font():
    return importlib.resources.files("aio_lanraragi_tests.resources.fonts.Roboto") / "Roboto-Regular.ttf"