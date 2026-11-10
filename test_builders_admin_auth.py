import tempfile
import unittest

from builders_shortener.admin_auth import (
    ADMIN,
    APPROVED,
    OWNER,
    PENDING,
    REJECTED,
    SUSPENDED,
    VIEWER,
    AdminAuthError,
    AdminAuthService,
    JsonAdminStore,
    verify_password,
)


class AdminAuthServiceTests(unittest.TestCase):
    def make_service(self, session_ttl_seconds=3600):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = JsonAdminStore(
            f"{temp_dir.name}/admin-state.json",
            f"{temp_dir.name}/admin-audit.jsonl",
        )
        return AdminAuthService(store, session_ttl_seconds=session_ttl_seconds)

    def test_bootstrap_owner_authenticates_without_plaintext_password_storage(self):
        service = self.make_service()
        owner = service.ensure_bootstrap_owner("Owner@Example.COM", "correct-horse")

        self.assertIsNotNone(owner)
        self.assertEqual(owner.username, "owner@example.com")
        self.assertEqual(owner.role, OWNER)
        self.assertTrue(verify_password("correct-horse", owner.password_hash))
        self.assertNotIn("correct-horse", owner.password_hash)

        token = service.authenticate("owner@example.com", "correct-horse")
        resolved = service.resolve_session(token)

        self.assertEqual(resolved.username, "owner@example.com")
        self.assertEqual(resolved.role, OWNER)

    def test_owner_manages_users_and_viewer_is_read_only(self):
        service = self.make_service()
        owner = service.ensure_bootstrap_owner("owner@example.com", "owner-pass")
        admin = service.create_user(owner, "admin@example.com", "admin-pass", ADMIN)
        viewer = service.create_user(owner, "viewer@example.com", "viewer-pass", VIEWER)

        users = service.list_users(viewer)

        self.assertEqual(
            {item["username"] for item in users},
            {"owner@example.com", "admin@example.com", "viewer@example.com"},
        )
        self.assertNotIn("password_hash", users[0])
        with self.assertRaises(AdminAuthError):
            service.create_user(viewer, "blocked@example.com", "blocked-pass", ADMIN)
        with self.assertRaises(AdminAuthError):
            service.delete_user(admin, "viewer@example.com")

    def test_only_approved_accounts_can_authenticate(self):
        service = self.make_service()
        owner = service.ensure_bootstrap_owner("owner@example.com", "owner-pass")
        for status in (PENDING, REJECTED, SUSPENDED):
            service.create_user(owner, f"{status}@example.com", "user-pass", VIEWER, status=status)

            with self.assertRaises(AdminAuthError):
                service.authenticate(f"{status}@example.com", "user-pass")

        approved = service.create_user(owner, "approved@example.com", "user-pass", VIEWER, status=APPROVED)
        token = service.authenticate("approved@example.com", "user-pass")

        self.assertEqual(approved.status, APPROVED)
        self.assertEqual(service.resolve_session(token).username, "approved@example.com")

    def test_status_updates_retain_history_and_invalidate_sessions(self):
        service = self.make_service()
        owner = service.ensure_bootstrap_owner("owner@example.com", "owner-pass")
        service.create_user(owner, "member@example.com", "member-pass", VIEWER, status=PENDING)

        approved = service.update_user(owner, "member@example.com", status=APPROVED)
        token = service.authenticate("member@example.com", "member-pass")
        suspended = service.update_user(owner, "member@example.com", status=SUSPENDED)

        self.assertEqual(approved.status_history[0]["status"], PENDING)
        self.assertEqual([item["status"] for item in suspended.status_history], [PENDING, APPROVED, SUSPENDED])
        self.assertFalse(suspended.active)
        with self.assertRaises(AdminAuthError):
            service.resolve_session(token)

    def test_user_updates_invalidate_existing_sessions(self):
        service = self.make_service()
        owner = service.ensure_bootstrap_owner("owner@example.com", "owner-pass")
        service.create_user(owner, "admin@example.com", "admin-pass", ADMIN)
        token = service.authenticate("admin@example.com", "admin-pass")

        service.update_user(owner, "admin@example.com", active=False)

        with self.assertRaises(AdminAuthError):
            service.resolve_session(token)

    def test_last_active_admin_cannot_be_demoted_deactivated_or_deleted(self):
        service = self.make_service()
        owner = service.ensure_bootstrap_owner("owner@example.com", "owner-pass")
        service.create_user(owner, "admin@example.com", "admin-pass", ADMIN)
        service.update_user(owner, "admin@example.com", active=False)

        with self.assertRaises(AdminAuthError):
            service.update_user(owner, "owner@example.com", role=VIEWER)
        with self.assertRaises(AdminAuthError):
            service.update_user(owner, "owner@example.com", status=SUSPENDED)
        with self.assertRaises(AdminAuthError):
            service.delete_user(owner, "owner@example.com")

        service.update_user(owner, "admin@example.com", status=APPROVED)
        service.update_user(owner, "owner@example.com", status=SUSPENDED)
        self.assertFalse(service.store.get_user("owner@example.com").active)

    def test_expired_session_is_rejected_and_removed(self):
        service = self.make_service(session_ttl_seconds=1)
        service.ensure_bootstrap_owner("owner@example.com", "owner-pass")
        token = service.authenticate("owner@example.com", "owner-pass")
        stored = service.store.get_session(token)
        stored.expires_at = 1
        service.store.save_session(stored)

        with self.assertRaises(AdminAuthError):
            service.resolve_session(token)

        self.assertIsNone(service.store.get_session(token))

    def test_audit_log_records_success_and_denied_outcomes(self):
        service = self.make_service()
        owner = service.ensure_bootstrap_owner("owner@example.com", "owner-pass")
        viewer = service.create_user(owner, "viewer@example.com", "viewer-pass", VIEWER)

        with self.assertRaises(AdminAuthError):
            service.create_user(viewer, "blocked@example.com", "blocked-pass", ADMIN)

        events = service.store.list_audit_events()
        outcomes = {(event.action, event.outcome) for event in events}

        self.assertIn(("user.bootstrap", "success"), outcomes)
        self.assertIn(("permission.check", "denied"), outcomes)


if __name__ == "__main__":
    unittest.main()
