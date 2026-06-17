# Example Pulumi Team RBAC

A Pulumi program that bootstraps a Pulumi Cloud team with a least-privilege,
tag-based custom role and a matching set of ESC environments. The role
follows the three-layer structure Pulumi Cloud expects — **permission sets
→ policy → role** — all backed by `pulumiservice.api.Role` resources
distinguished by their `uxPurpose` and composed by ID. Tag conditions
(`team=<team>` AND `env=<env>`) gate each tier of access (full / read-write
/ read-only) so the role grants the right scopes automatically whenever a
stack or ESC env is tagged appropriately.

The default `teamName=billing` evokes a finance team owning
cost-management stacks and environments; the patterns adapt to any other
team / domain pair.

## What gets deployed

With `teamName=billing` (the default) the program produces:

- One Pulumi team — `billing`
- Four ESC environments at `<org>/billing/{sbx,dev,stage,prod}`, each
  tagged `team=billing` and `env=<env>`
- Seven permission sets (`uxPurpose=set`): one `base` allow plus a
  stack and env triplet (full / write / read) for sbx/dev/stage
- One policy (`uxPurpose=policy`) — a `PermissionDescriptorGroup` whose
  entries compose the sets by ID, gated on tag conditions, plus a
  `shared=true` override granting env-read on any ESC env so flagged
- One role (`uxPurpose=role`) — `billing-app` — composing the policy
- One `TeamRoleAssignment` binding the role to the team

prod is intentionally not in the role's conditionals — the prod ESC env
still exists (created via the base set's `environment:create`), but the
role grants no per-env access beyond the base.

### Resources

| Logical name (Pulumi)       | Resource type                              | Purpose                                                                                |
| --------------------------- | ------------------------------------------ | -------------------------------------------------------------------------------------- |
| `team`                      | `pulumiservice:index:Team`                 | The Pulumi team that will receive the role.                                            |
| `env-<team>-{sbx,dev,stage,prod}` | `pulumiservice:index:Environment`     | One ESC env per deployment env, under `<team>/<env>`.                                  |
| `env-<team>-<env>-tag-team` | `pulumiservice:api/esc:EnvironmentTag`     | `team=<team>` tag on each ESC env, matched by the role's tag conditions.               |
| `env-<team>-<env>-tag-env`  | `pulumiservice:api/esc:EnvironmentTag`     | `env=<env>` tag on each ESC env, matched by the role's tag conditions.                 |
| `perm-set-base`             | `pulumiservice:api:Role` (uxPurpose=set)   | Unconditional bootstrap scopes (`stack:create`, `environment:create`).                 |
| `perm-set-stack-full`       | `pulumiservice:api:Role` (uxPurpose=set)   | Full stack scopes — gated on `team`+`env=sbx`.                                         |
| `perm-set-stack-write`      | `pulumiservice:api:Role` (uxPurpose=set)   | Read/write stack scopes — gated on `team`+`env=dev`.                                   |
| `perm-set-stack-read`       | `pulumiservice:api:Role` (uxPurpose=set)   | Read-only stack scopes — gated on `team`+`env=stage`.                                  |
| `perm-set-env-full`         | `pulumiservice:api:Role` (uxPurpose=set)   | Full ESC env scopes — gated on `team`+`env=sbx`.                                       |
| `perm-set-env-write`        | `pulumiservice:api:Role` (uxPurpose=set)   | Read/write ESC env scopes — gated on `team`+`env=dev`.                                 |
| `perm-set-env-read`         | `pulumiservice:api:Role` (uxPurpose=set)   | Read-only ESC env scopes — gated on `team`+`env=stage` and on `shared=true`.           |
| `team-policy`               | `pulumiservice:api:Role` (uxPurpose=policy)| `PermissionDescriptorGroup` composing the sets via tag-based conditional Composes.     |
| `team-role`                 | `pulumiservice:api:Role` (uxPurpose=role)  | The user-assignable role — single Compose of the policy. Cloud-side name: `<team>-app`.|
| `team-role-binding`         | `pulumiservice:index:TeamRoleAssignment`   | Binds `team-role` to `team`.                                                           |

## Config inputs

| Config key   | Purpose                                                                                                | Default   |
| ------------ | ------------------------------------------------------------------------------------------------------ | --------- |
| `teamName`   | Pulumi team to create, ESC project namespace (envs land at `<teamName>/<env>`), and role prefix.        | `billing` |

The Pulumi Cloud organization is read from the stack's `org/project/stack`
reference via `pulumi.get_organization()` — set via `pulumi stack init
<org>/<stack>`, not through config.

## Quick start

```sh
# 1. Clone
git clone <repo-url> example-pulumi-team-rbac
cd example-pulumi-team-rbac

# 2. Install Python deps via Poetry (creates a managed virtualenv)
poetry install

# 3. Install the git hooks (lint/format + commit-message validation)
pre-commit install
pre-commit install --hook-type commit-msg

# 4. Point Pulumi at your org and create a stack
pulumi login                          # if not already authenticated
pulumi stack init <org>/<stack>       # replace <org> with your Pulumi org

# 5. (Optional) override the default team name
pulumi config set teamName billing

# 6. Preview, then deploy
pulumi preview
pulumi up
```

## Referencing the deployed resources from another program

Stack outputs are emitted as flat snake_case keys (e.g. `role_id`,
`permission_set_stack_full_id`, `environment_sbx_name`) so downstream
programs can read them directly via `get_output(...)` without an
`.apply()` to drill into nested dicts. ESC env `*_name` outputs are the
fully-qualified `<org>/<project>/<env>` reference, ready to use as an
import.

```python
"""Example consumer program."""

import pulumi
from pulumi_pulumiservice import Stack, StackTag, TeamRoleAssignment

# Point at the stack that owns the team + role + envs. Replace:
#   <org>   — your Pulumi Cloud organization
#   <stack> — the stack name this program was deployed under
#             (e.g. `dev`, `prod`)
billing = pulumi.StackReference("<org>/example-pulumi-team-rbac/<stack>")

team_name      = billing.get_output("team_name")
role_id        = billing.get_output("role_id")
stack_full_id  = billing.get_output("permission_set_stack_full_id")
sbx_env_ref    = billing.get_output("environment_sbx_name")   # "<org>/billing/sbx"

# Create a new stack and tag it so the billing role's tag-conditional
# rules match it (team=billing + env=dev → dev-write tier applies).
my_stack = Stack(
    "billing-checkout-dev",
    organization_name="<org>",
    project_name="billing-checkout",
    stack_name="dev",
)
StackTag("billing-checkout-tag-team",
    organization=my_stack.organization_name,
    project=my_stack.project_name,
    stack=my_stack.stack_name,
    name="team",
    value=team_name,
)
StackTag("billing-checkout-tag-env",
    organization=my_stack.organization_name,
    project=my_stack.project_name,
    stack=my_stack.stack_name,
    name="env",
    value="dev",
)

# Or assign the role to another team that needs the same access surface.
TeamRoleAssignment(
    "shadow-team-binding",
    organization_name="<org>",
    team_name="billing-shadow",
    role_id=role_id,
)

pulumi.export("checkoutTagsApplied", team_name)
pulumi.export("sbxEnvImportRef", sbx_env_ref)
```

Full list of output keys (each is a top-level scalar — no `.apply()`
needed):

- `team_name`
- `role_name`, `role_id`
- `policy_name`, `policy_id`
- `permission_set_<key>_name` / `permission_set_<key>_id` for `<key>` in
  `base`, `stack_full`, `stack_write`, `stack_read`, `env_full`,
  `env_write`, `env_read`
- `environment_<env>_name` / `environment_<env>_id` for `<env>` in
  `sbx`, `dev`, `stage`, `prod`

## Developer setup

### Prerequisites

| Tool         | Why                                                                  |
| ------------ | -------------------------------------------------------------------- |
| `pulumi`     | The Pulumi CLI — runs the program and manages stack state.          |
| `python`     | Python ≥ 3.10 — the program's runtime.                              |
| `poetry`     | Dependency / virtualenv manager for the program.                    |
| `pre-commit` | Runs lint, format, and commit-message hooks on `git commit`.        |
| `git`        | Version control.                                                     |

VS Code is optional but recommended — the workspace's
`.vscode/settings.json` enables ruff format-on-save out of the box, and
`.vscode/extensions.json` prompts to install the Ruff, Python, and
EditorConfig extensions.

### Install on macOS via Homebrew

```sh
brew install pulumi/tap/pulumi python poetry pre-commit git
```

### Install on other platforms

- **Pulumi**: <https://www.pulumi.com/docs/iac/download-install/>
- **Python ≥ 3.10**: your package manager or <https://www.python.org/downloads/>
- **Poetry**: <https://python-poetry.org/docs/#installation>
- **pre-commit**: `pipx install pre-commit` or <https://pre-commit.com/#install>
- **git**: your package manager

### Commit message format

Commits are validated against the [Conventional Commits](https://www.conventionalcommits.org/)
spec via `commitizen` (wired through `pre-commit`'s `commit-msg` hook).
Use one of: `build`, `bump`, `chore`, `ci`, `docs`, `feat`, `fix`,
`perf`, `refactor`, `revert`, `style`, `test`. Examples:

```text
feat(role): add shared=true env-read override
fix: bump max line length to ruff default
docs: clarify three-layer role architecture
```
