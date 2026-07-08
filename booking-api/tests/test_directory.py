"""Tests for attendee directory search endpoint (Task 11).

TDD: Written BEFORE implementation — all tests must fail (404) initially,
then pass after implementation.
"""
import uuid

import pytest

from tests.conftest import make_token, authed_client, make_user


class TestDirectorySearch:
    async def test_q_matches_by_partial_name(self, admin, test_engine, db_session):
        """Query parameter q matches users by partial full_name (case-insensitive)."""
        user, client = admin
        # Create test users with unique identifier
        unique_suffix = uuid.uuid4().hex[:8]
        alice_name = f"AliceDir{unique_suffix}"
        alice = await make_user(test_engine, full_name=alice_name, email="alice@test.com", role="requester")

        # Search for unique part of the name — case insensitive partial match
        resp = await client.get(f"/api/v1/users/directory?q=alicedir{unique_suffix}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data) >= 1
        # Find our specific user in results
        found = [u for u in data if u["id"] == str(alice.id)]
        assert len(found) == 1
        assert found[0]["full_name"] == alice_name
        assert found[0]["email"] == "alice@test.com"
        assert "id" in found[0]

    async def test_q_matches_by_email(self, admin, test_engine, db_session):
        """Query parameter q matches users by partial email (case-insensitive)."""
        user, client = admin
        # Create test users with unique identifiers
        unique_suffix = uuid.uuid4().hex[:8]
        alice_email = f"alice_{unique_suffix}@domain.com"
        alice = await make_user(test_engine, full_name="Alice Cooper", email=alice_email, role="requester")

        # Search for the unique part of the email — should find the user
        resp = await client.get(f"/api/v1/users/directory?q=alice_{unique_suffix}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data) >= 1
        # Find our specific user in results
        found = [u for u in data if u["id"] == str(alice.id)]
        assert len(found) == 1
        assert found[0]["full_name"] == "Alice Cooper"
        assert found[0]["email"] == alice_email

    async def test_inactive_user_excluded(self, admin, test_engine, db_session):
        """Inactive users (is_active=False) are excluded from results."""
        user, client = admin
        # Create an active and an inactive user with unique identifiers
        factory_session = db_session
        unique_suffix = uuid.uuid4().hex[:8]
        active_name = f"ActiveInactiveTest{unique_suffix}"

        active_user = await make_user(test_engine, full_name=active_name, email="active@test.com", role="requester")
        # Create an inactive user directly via db_session
        from app.models.user_mirror import User
        inactive_user = User(
            id=uuid.uuid4(),
            full_name=f"InactiveInactiveTest{unique_suffix}",
            email="inactive@test.com",
            role="requester",
            is_active=False,
        )
        factory_session.add(inactive_user)
        await factory_session.flush()

        # Search for the unique suffix — should only return active user, not inactive
        resp = await client.get(f"/api/v1/users/directory?q={unique_suffix}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Find users matching our suffix
        matching = [u for u in data if unique_suffix in u["full_name"]]
        # Should only have the active user
        assert len(matching) == 1
        assert matching[0]["full_name"] == active_name

    async def test_user_with_empty_email_excluded(self, admin, test_engine, db_session):
        """Users with empty email are excluded from results."""
        user, client = admin
        # Create a user with email and one without
        factory_session = db_session
        unique_suffix = uuid.uuid4().hex[:8]
        with_email_name = f"UserWithEmailTest{unique_suffix}"

        with_email = await make_user(test_engine, full_name=with_email_name, email="email@test.com", role="requester")
        # Create a user with empty email directly
        from app.models.user_mirror import User
        without_email = User(
            id=uuid.uuid4(),
            full_name=f"UserNoEmailTest{unique_suffix}",
            email="",  # empty email
            role="requester",
            is_active=True,
        )
        factory_session.add(without_email)
        await factory_session.flush()

        # Search for the unique suffix — should only return user with non-empty email
        resp = await client.get(f"/api/v1/users/directory?q={unique_suffix}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        matching = [u for u in data if unique_suffix in u["full_name"]]
        # Should only have user with email
        assert len(matching) == 1
        assert matching[0]["full_name"] == with_email_name
        assert matching[0]["email"] == "email@test.com"

    async def test_empty_q_returns_results(self, admin, test_engine, db_session):
        """Empty q parameter returns up to 50 results."""
        user, client = admin
        # Create multiple test users
        users_created = []
        for i in range(10):
            u = await make_user(
                test_engine,
                full_name=f"User {i:02d}",
                email=f"user{i:02d}@test.com",
                role="requester"
            )
            users_created.append(u)

        # Search with empty q (or no q parameter)
        resp = await client.get("/api/v1/users/directory")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Should return multiple users (at least the 10 we created)
        assert len(data) >= 10
        # All returned users should be active and have non-empty emails
        for item in data:
            assert "id" in item
            assert "full_name" in item
            assert "email" in item
            assert item["email"] != ""

    async def test_results_sorted_by_full_name(self, admin, test_engine, db_session):
        """Results are sorted by full_name."""
        user, client = admin
        # Create users with names that sort in a specific order
        unique_suffix = uuid.uuid4().hex[:8]
        zebra = await make_user(test_engine, full_name=f"ZSort{unique_suffix}", email="zebra@test.com", role="requester")
        alice = await make_user(test_engine, full_name=f"ASort{unique_suffix}", email="alice@test.com", role="requester")
        bob = await make_user(test_engine, full_name=f"BSort{unique_suffix}", email="bob@test.com", role="requester")

        # Search with the unique suffix to get only our test users
        resp = await client.get(f"/api/v1/users/directory?q={unique_suffix}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Find our three users in the results
        names = [item["full_name"] for item in data if unique_suffix in item["full_name"]]
        # Should be sorted alphabetically
        assert len(names) == 3
        assert names == sorted(names)

    async def test_limit_50_results(self, admin, test_engine, db_session):
        """Results are limited to 50 even if there are more."""
        user, client = admin
        # Create 60 test users
        for i in range(60):
            await make_user(
                test_engine,
                full_name=f"Test User {i:03d}",
                email=f"user{i:03d}@test.com",
                role="requester"
            )

        resp = await client.get("/api/v1/users/directory")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Should be limited to 50
        assert len(data) == 50

    async def test_requires_authentication(self, client, db_session):
        """Unauthenticated request returns 401."""
        resp = await client.get("/api/v1/users/directory")
        assert resp.status_code == 401 or resp.status_code == 403, resp.text
