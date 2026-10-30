# Milestone release readiness

On every planning pass, decide whether the current release milestone is ready
to publish. Make this assessment even if the todo queue is full or the planner
cannot change an issue. It is a read-only decision, not a release operation.

1. Find the current milestone and its release candidate from the repository's
   documented release plan, milestone description, issues, and existing release
   records. Use the explicit product/version mapping. A milestone title or a
   closed issue alone does not prove a candidate or authorize a release. If the
   scope, candidate or version mapping is missing or ambiguous, report `HOLD`.
2. Read the full milestone issue inventory, including closed work, and the
   linked pull requests, dependencies and decisions. Follow pagination. Check
   the actual merged commits and unresolved work against the release scope.
   An open or unknown requirement that the release contract requires is a
   blocker. A partial PR is not proof that its parent requirement is complete.
3. Read the repository's actual release contract and its complete required
   evidence for the exact candidate commit or artifact. Check applicable CI,
   unit, functional, E2E, performance, security, release approvals, and other
   gates when that contract requires them. Verify current run identities,
   results and artifact bindings. A green subset does not prove the full gate.
4. Return `READY` only if the documented release scope and every applicable
   requirement have positive, current evidence for the same candidate. A failed,
   missing, skipped, cancelled, pending, stale, partial or unverifiable result
   is `HOLD`. Never lower a threshold or invent a release contract or approval.
5. Report the repository, milestone number and title, exact candidate and
   version, `READY` or `HOLD`, links to the contract and evidence, and each
   blocker or missing decision. If an item cannot be identified, say so.
   Recheck the candidate and material evidence before reporting `READY`.

Do not create tags, publish packages or releases, change versions, close a
milestone, change its scope, or alter another session's schedule. Existing
approval and release ownership remain in force. A `READY` report is a planning
assessment, not a substitute for the release system's final checks or approval.
