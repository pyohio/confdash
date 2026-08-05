"""Shared helper for testing import-time settings branching.

Several settings decisions happen while `project.settings` is being imported: which email backend to
use, which hosts to allow. Overriding settings afterwards cannot exercise that logic, so these tests
reimport the module under a modified environment instead.
"""

import importlib
import os
from contextlib import contextmanager


@contextmanager
def settings_env(**overrides):
    """Reimport the settings module with the given environment applied.

    A value of None removes the variable. The module is reloaded again on exit so later tests see
    the real configuration.
    """
    saved = {key: os.environ.get(key) for key in overrides}
    os.environ.update({k: v for k, v in overrides.items() if v is not None})
    for key, value in overrides.items():
        if value is None:
            os.environ.pop(key, None)
    try:
        import project.settings as settings_module

        yield importlib.reload(settings_module)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        import project.settings as settings_module

        importlib.reload(settings_module)
