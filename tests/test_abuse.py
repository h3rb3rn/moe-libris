"""Tests for the abuse prevention system — strikes, rate limiting, blocking."""

import pytest
import pytest_asyncio

from app.services.abuse import (
    record_strike, get_strikes, get_all_strikes,
    should_rate_limit, should_block, clear_strikes,
    check_rate_limit,
)


@pytest_asyncio.fixture
async def clean_node():
    """Provide a clean node ID and clear strikes after test."""
    from app.services.abuse import close_valkey
    # Reset the connection for each test to avoid event loop issues
    await close_valkey()
    node_id = "test-abuse-node"
    await clear_strikes(node_id)
    yield node_id
    await clear_strikes(node_id)
    await close_valkey()


class TestStrikeSystem:
    @pytest.mark.asyncio
    async def test_initial_strikes_zero(self, clean_node):
        strikes = await get_strikes(clean_node)
        assert strikes == 0

    @pytest.mark.asyncio
    async def test_record_strike_increments(self, clean_node):
        count = await record_strike(clean_node, "syntax")
        assert count == 1
        count = await record_strike(clean_node, "syntax")
        assert count == 2

    @pytest.mark.asyncio
    async def test_separate_categories(self, clean_node):
        await record_strike(clean_node, "syntax")
        await record_strike(clean_node, "security")
        all_strikes = await get_all_strikes(clean_node)
        assert all_strikes["syntax"] == 1
        assert all_strikes["security"] == 1
        assert all_strikes["general"] == 0

    @pytest.mark.asyncio
    async def test_clear_strikes(self, clean_node):
        await record_strike(clean_node, "syntax")
        await record_strike(clean_node, "security")
        await clear_strikes(clean_node)
        all_strikes = await get_all_strikes(clean_node)
        assert sum(all_strikes.values()) == 0


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_no_rate_limit_below_threshold(self, clean_node):
        assert not await should_rate_limit(clean_node)

    @pytest.mark.asyncio
    async def test_rate_limit_at_threshold(self, clean_node):
        for _ in range(3):
            await record_strike(clean_node, "general")
        assert await should_rate_limit(clean_node)


class TestBlocking:
    @pytest.mark.asyncio
    async def test_no_block_below_threshold(self, clean_node):
        for _ in range(2):
            await record_strike(clean_node, "general")
        assert not await should_block(clean_node)

    @pytest.mark.asyncio
    async def test_block_at_threshold(self, clean_node):
        for _ in range(10):
            await record_strike(clean_node, "general")
        assert await should_block(clean_node)

    @pytest.mark.asyncio
    async def test_security_strikes_weighted_3x(self, clean_node):
        """Security strikes count 3x — 4 security strikes = 12 weighted = blocked."""
        for _ in range(4):
            await record_strike(clean_node, "security")
        assert await should_block(clean_node)

    @pytest.mark.asyncio
    async def test_mixed_strikes_below_block(self, clean_node):
        """2 syntax + 2 security = 2 + 6 = 8 < 10 = not blocked."""
        await record_strike(clean_node, "syntax")
        await record_strike(clean_node, "syntax")
        await record_strike(clean_node, "security")
        await record_strike(clean_node, "security")
        assert not await should_block(clean_node)

    @pytest.mark.asyncio
    async def test_mixed_strikes_at_block(self, clean_node):
        """2 syntax + 3 security = 2 + 9 = 11 >= 10 = blocked."""
        await record_strike(clean_node, "syntax")
        await record_strike(clean_node, "syntax")
        await record_strike(clean_node, "security")
        await record_strike(clean_node, "security")
        await record_strike(clean_node, "security")
        assert await should_block(clean_node)
