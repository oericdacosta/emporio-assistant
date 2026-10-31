from emporio import __version__


def test_version_has_semver_shape() -> None:
    assert __version__.count(".") == 2
