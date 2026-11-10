# Short-link admin MVP requirements

## Goal

Add a protected, semi-private management UI and API to the existing Firestore-backed short-link service. The MVP prioritizes fast single and batch creation, safe ownership boundaries, and operational clarity. UTM automation and detailed analytics are intentionally deferred.

## R-F1: Protected semi-private access

- Given a visitor is unauthenticated or their account is not approved, when they access an admin UI or admin API route, then the request is denied without exposing protected link or user data.
- Given an approved member or administrator is authenticated, when they access an allowed route, then the service grants only the permissions assigned to their role.
- Given the system has users with pending, approved, rejected, or suspended status, when an administrator manages access, then status history is retained and the system never permits the last administrator to be removed or demoted.

## R-F2: Link creation and alias uniqueness

- Given an authenticated user submits a valid landing URL and optional alias, when link creation succeeds, then the target and alias are durably stored and the response contains the usable short URL.
- Given two links have the same landing URL, when either link is created, then duplicate landing URLs are allowed.
- Given an alias is already assigned globally, when another row requests that alias, then creation fails with an explicit alias-conflict error and never overwrites or silently changes the existing link.

## R-F3: Single, batch, and lifecycle operations

- Given a user has one URL or multiple pasted/CSV rows, when they submit the form, then the system supports both single creation and batch creation with row-level success or error results.
- Given a requested alias is malformed, reserved, or outside the supported character/length policy, when the row is validated, then that row is rejected before persistence with an actionable error.
- Given an existing link is deactivated, when its public short URL is requested, then it returns not-found while the link and click history remain available to administrators.
- Given an existing Firestore short link predates the admin UI, when it is listed, then it remains usable and is represented as a legacy/unassigned record rather than being migrated or overwritten automatically.

## R-F4: Cached domain-safety policy

- Given a landing URL is normalized, when its safety decision is requested, then the decision is cached by a stable URL hash with a finite TTL and a fresh cache entry avoids an external Web Risk lookup.
- Given the safety provider returns a positive threat result, when a link is created or an existing link is resolved, then the policy blocks the unsafe operation.
- Given the safety provider times out or errors, when a link is created or reviewed, then the result is recorded as review-needed for administrator treatment rather than causing an unbounded per-request dependency.

## R-F5: Authorization, compatibility, and auditability

- Given a member attempts an administrator-only action, when the API receives the request, then it returns forbidden and makes no state change.
- Given an administrator creates, updates, deactivates, or manages access, when the action completes, then the actor, action, target, outcome, and timestamp are recorded in an audit trail.
- Given existing clients use the established public redirect route, when the admin surface is introduced, then the existing `/s/<slug>` redirect contract and existing Firestore data remain compatible.

## R-F6: Verification and future intent

- Given the implementation is checked in the repository, when the documented test commands are run, then focused regression tests and the existing full suite provide repeatable evidence for the MVP behavior.
- Given future work adds UTM handling, detailed analytics, or multiple short-link domains, when those features are considered, then the current MVP boundaries and extension points are documented without enabling them by accident.
