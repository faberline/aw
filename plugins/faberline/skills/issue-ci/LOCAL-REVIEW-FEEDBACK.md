# Local review failure feedback

This is an opt-in for a repository that uses local issue review and the
CI-owned development reporter. The trusted default-branch policy must contain
`"local_review_feedback": {"version": 1, "reviewer_ids": [123]}`. Replace
the example with the numeric GitHub IDs authorized to publish local reviews.
The existing development reporter must accept the `aw-development-reconcile`
repository-dispatch event. CI remains the only lifecycle-label writer.

The reviewer records a failure for the exact issue, PR head and PR base. The
review tool then wakes CI. CI rereads the trusted reviewer identity, receipt,
issue and PR. It posts the failure link to the issue and returns eligible work
to `todo`. A repeated wake cannot take an issue away from a worker that has
already claimed it. A changed base or head needs a new review. A successful
later comment does not erase a failure for the same head and base.

A GitHub user ID authenticates the account that posted a receipt. It does not
prove a reviewer was independent of the author. The local review workflow must
separately enforce that requirement. Do not enable this setting until the
review tool and the CI reporter both use the reviewed implementation.
