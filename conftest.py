"""Global pytest configuration.

Windows / pytest>=9 quirk: when ``--basetemp`` is passed and its *parent*
directory does not exist, the tmpdir factory fails with
``FileNotFoundError ... os.mkdir(path, mode=448, parents=False)`` during the
setup of the first test that requests ``tmp_path``.  Pre-creating the parent
directory avoids the failure for every suite.
"""

import os


def pytest_configure(config):
    basetemp = config.getoption("basetemp")
    if basetemp:
        parent = os.path.dirname(os.path.abspath(str(basetemp)))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
