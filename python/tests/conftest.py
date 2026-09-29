import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from vcore.lean import VCore  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples" / "coding-agent"

# Random cases per differential test; raise it for evaluation runs.
CASES = int(os.environ.get("VCORE_CASES", "200"))


@pytest.fixture(scope="session")
def vcore():
    with VCore() as v:
        yield v


@pytest.fixture(scope="session")
def example():
    return lambda name: json.loads((EXAMPLES / name).read_text())
