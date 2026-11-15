# Cost control for the shared GKE test cluster

Status: **guideline, 2026-11-15**. It has no checker yet.

The shared test cluster `faberline-test` costs the owner money (TWD). Cost
control is a standing requirement. It does not end when one bill is low.
Cluster setup is in [test-cluster.md](test-cluster.md). CI design is in
[ci-on-gke.md](ci-on-gke.md).

## Rules

1. **Cost is a requirement.** Every change that starts GKE nodes states its
   expected cost in the pull request or issue.
2. **Select by diff.** A pull request runs only the GKE suites that its diff
   can affect. A docs-only pull request starts no GKE node. `main` and
   release candidates run the full set.
3. **Cheapest valid machine.** Use the cheapest machine type and Spot nodes
   that still give a valid result. A larger machine or an on-demand node
   needs a written reason in `infra/gke-test/pools.yaml`.
4. **Scale to zero.** The minimum of every node pool is 0 nodes. The owner
   accepts a slower cold start. A cold start is not a reason to keep a node.
   A minimum above 0 needs a written reason in `infra/gke-test/pools.yaml`
   and the owner's approval. A job deletes its namespace when it ends. No job
   keeps a node only to wait.
5. **Rerun the failed work only.** A rerun starts only the failed work when
   the tooling permits it.
6. **Approval first.** These need the owner's approval before they start,
   with a number in TWD: a new paid run, a new node pool, a higher quota, a
   new bucket, or any cost above the approved cap. An approved cap is for the
   named run only. Stop and report at 80% of the cap.
7. **Weekly review.** Each week, record the node core-hours of each pool and
   the bill. Take core-hours from Cloud Monitoring:
   `kubernetes.io/node/cpu/total_cores`, grouped by node name. The budget
   alert is 3,000 TWD a month.
8. **No weaker tests.** These rules never remove or weaken a test, a
   threshold, a coverage ratchet or a timeout. Reduce cost by selection,
   sharing and cheaper machines, not by less coverage.

## Not yet available

The BigQuery billing export is not enabled. Cost in TWD for each pool is an
estimate until it is enabled.

## Review log

### 2026-11-15 (baseline)

Measured by the aw service desk, from 2026-11-06 to 2026-11-14:

- The daily bill grew from about 89 to about 460 TWD. The total for ten days
  was about 2,530 TWD. The budget alert is 3,000 TWD a month.
- Node use was about 9,250 core-hours: lumen 45%, relay 23%, tape 16%,
  defer 10%, system-pool 5%.
- About 85 TWD a day is fixed (cluster fee and NAT). This is not verified.
- More than 4,000 nodes started. A node lives about one hour.
