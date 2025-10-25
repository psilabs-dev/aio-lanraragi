from aio_lanraragi_tests.s1.utils import load_dataset


def test_load_dataset_shapes():
    data = load_dataset()
    assert isinstance(data, dict)
    assert "archives" in data and "categories" in data
    assert len(data["archives"]) == 1000
    assert len(data["categories"]) == 12
