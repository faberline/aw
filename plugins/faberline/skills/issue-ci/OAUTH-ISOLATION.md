# Personal-subscription OAuth isolation contract (offline candidate)

## Supported scope

`claude_final_review.credential_profile` must be
`personal-claude-max-oauth`: a personal Max setup bound by the deployment
owner to the opaque Actions credential. This is supported-profile selection,
not proof of token identity or an automatically verified account attestation.
No token inspection, authentication probe or API-key fallback is performed.
Changing the credential to another account/profile requires reviewing that
binding again. Unknown or Team/Enterprise profiles are refused before CLI start.

The official [server-managed settings](https://code.claude.com/docs/en/server-managed-settings)
documentation limits that delivery to Team/Enterprise. It is therefore not a
standing unknown hold for a credential known to come from a personal Max setup.
The owner must confirm the token came from that same setup; CLI login status
alone does not prove the origin of a separately supplied token. If that binding
has not been supplied, the one missing user fact is: did this opaque credential
come from the same personal Max setup? No organization admin UI is required
for a personal account.

## Verifiable runner boundary

Review runs only in the existing action's Linux workflow_run path. Before
credential handling or CLI launch, `endpoint_policy_preflight` requires the
supported profile and checks `/etc/claude-code` with lstat. An existing directory,
file or dangling symlink refuses. Permission/I/O errors are unknown and refuse;
only positive ENOENT absence passes. Policy contents are not read or edited.
This is machine-policy evidence, separately from the owner-provided account
binding and the documented server-managed eligibility limit.

The fixed CLI is byte-pinned through reviewed policy. It receives a fresh
temporary HOME/config directory/cwd, minimal environment, no GitHub token or
API key, `--safe-mode`, no tools, empty strict MCP configuration, no user/project/
local setting sources and explicit disableAllHooks. Safe mode is defense for
non-managed customizations; it is not evidence that managed hooks are disabled.
The model sees bounded API-fetched data, not a checkout or arbitrary executable.

## Version/capability facts

Local 2.1.310 `--help` confirms safe mode retains normal authentication and
managed policy, whereas bare mode does not read OAuth. Bare mode is not used.
Official [CLI reference](https://code.claude.com/docs/en/cli-reference) and
[configuration debugging](https://code.claude.com/docs/en/debug-your-config)
explicitly retain managed hooks under safe mode. Ordinary CLI disableAllHooks
cannot override that tier; see the [hooks reference](https://code.claude.com/docs/en/hooks).
No undocumented environment switch or caller-supplied verified boolean is used.

If a future credential is eligible for server-managed delivery, that is outside
this pilot. In its same trusted account/session, the user can inspect the
`Managed settings (remote)` outcome from `claude doctor` and Setting sources in
`/status`. Team/Enterprise policy inspection belongs to its Owner/Primary Owner
at Organization settings → Claude Code → Managed settings. This is a finite
setup investigation for an applicable account, not a blanket request to a Max
user and not automatic acceptance of a pasted attestation.

## Independent test-contract boundary

Consumer policy must protect direct assertion/harness and target/build sources.
Tape holds its integration-test tree, Cargo manifests/lock, build hooks, .cargo
and toolchain selectors, in addition to existing CI enforcer roots. Changes go
to separate independent test/gate-contract review before any model call. Normal
product source stays reviewable. Synthetic unit tests cover protected changes,
renames, normal product source, unsupported profiles and unknown endpoint policy.

No service, token validity call, installation, deployment, merge or schedule
change has occurred from this local contract/test work.
