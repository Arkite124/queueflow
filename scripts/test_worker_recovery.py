"""Run only recovery tests against explicitly supplied isolated services."""
import os
import sys

import pytest

if not os.getenv("TEST_DATABASE_URL") or not os.getenv("TEST_REDIS_URL"):
    raise SystemExit("Set TEST_DATABASE_URL and TEST_REDIS_URL for isolated PostgreSQL/Redis services first.")
sys.exit(pytest.main(["tests/integration", "-m", "recovery", "-v", *sys.argv[1:]]))
