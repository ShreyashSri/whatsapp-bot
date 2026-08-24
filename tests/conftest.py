"""Shared test fixtures."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_random_snark():
    """Keep the general post-command bonus-snark (features.natural_language)
    from nondeterministically firing in unrelated tests.

    It's driven by a bare random.random() check against GENERAL_SNARK_CHANCE
    on the shared, unseeded global random state -- without this, any test
    that exercises a successful NL command and asserts on
    client.send_message.call_args/call_count can flake depending on
    whatever the process's random state happens to be. Tests that actually
    want to exercise the snark path patch random.random themselves inside a
    narrower `with` block, which overrides this for their duration.
    """
    with patch("features.natural_language.random.random", return_value=1.0):
        yield
