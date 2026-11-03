import pytest
from fakes import TEST_DATABASE_URL

from app import db


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def pool():
    """A pool on the test database, emptied before each test."""
    pool = await db.create_pool(TEST_DATABASE_URL)
    await pool.execute("TRUNCATE messages, conversations RESTART IDENTITY")
    try:
        yield pool
    finally:
        await pool.close()
