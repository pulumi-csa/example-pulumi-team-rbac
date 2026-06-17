"""Example team RBAC bootstrap.

Creates an example Pulumi Cloud team, a set of ESC environments for
configuration management, and a least-privilege custom role that scopes
access to the team's stacks and ESC envs via tag-based criteria. The role
follows the three-layer structure Pulumi Cloud expects — permission sets
→ policy → role — all backed by `api.Role` resources distinguished by
their `uxPurpose` and composed by ID.

The team name is configurable via the `teamName` config key; it defaults
to `billing` to represent a use case of a finance team owning
cost-management stacks and environments. The patterns adapt to any other
team / domain pair — the examples below assume the default.

What this program creates (assuming `teamName=billing`):

1. A `billing` Pulumi team.
2. Four ESC environments — `billing/{sbx,dev,stage,prod}` — each tagged
   `team=billing` and `env=<env>`.
3. Seven permission sets (`uxPurpose=set`): one `base` allow + a stack
   and ESC env triplet (full / write / read) for the three regulated
   envs (sbx/dev/stage).
4. One policy (`uxPurpose=policy`) — a `PermissionDescriptorGroup`
   whose entries are:
       - an unconditional `Compose` of the base set,
       - six `Condition(Compose)` entries gating each scoped set on the
         appropriate `team=<team>` + `env=<env>` tag pair (stack and
         env contexts kept separate, as the tag-context expressions are
         per-entity-type),
       - one `Condition(Compose)` granting env-read on any ESC env
         tagged `shared=true`.
5. The top-level `billing-app` role (`uxPurpose=role`) — a single
   `Compose` referencing the policy.
6. A `TeamRoleAssignment` binding the role to the team.

prod is intentionally not in the role's conditionals. The prod ESC env
still exists (the team can create it via the base set's
`environment:create`), but the role grants no per-env access beyond the
base.
"""

from typing import Any, Mapping

import pulumi
import pulumi_pulumiservice.api as ps_api
from pulumi_pulumiservice import (
    Environment,
    Team,
    TeamRoleAssignment,
)
from pulumi_pulumiservice.api.esc import EnvironmentTag


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

config = pulumi.Config()
# Pulled from the stack's `org/project/stack` reference so the program
# always targets the org it's deployed to.
organization_name = pulumi.get_organization()
team_name = config.get("teamName") or "billing"
# ESC envs share their project namespace with the team — `<team>/<env>`.
project_name = team_name

# Deployment envs we create ESC environments for.
DEPLOYMENT_ENVS = ["sbx", "dev", "stage", "prod"]

# Envs the role's tag-based conditionals reference — matches the ref.
ROLE_CONDITIONAL_ENVS = ["sbx", "dev", "stage"]

TEAM_TAG_KEY = "team"
ENV_TAG_KEY = "env"
SHARED_TAG_KEY = "shared"


# ----------------------------------------------------------------------------
# Permission-set scope catalogs
#
# These scope lists define the permissions the team is granted on each
# stack or ESC env, tiered by the `env` tag value (full for sbx, read/write
# for dev, read for stage) — the policy's tag-conditional rules pick the
# right list for each (team, env) pair.
# ----------------------------------------------------------------------------

# Bootstrap scopes the team always holds regardless of tags. Lets the team
# create the first stack and ESC env in each app project — tag-gated rules
# can only check tags on already-existing entities, so unconditional create
# rights have to live outside the conditional tree.
#
# Docs: https://www.pulumi.com/docs/administration/access-identity/rbac/scopes/
_BASE_SCOPES = [
    "stack:create",
    "environment:create",
]

# sbx tier (full): everything a stack owner can do on a tagged stack —
# read/write state, import/export, manage encrypted secrets, rename, delete,
# transfer, cancel updates, manage stack access grants, and run/configure
# deployments and deployment schedules.
#
# Docs: https://www.pulumi.com/docs/administration/access-identity/rbac/scopes/stacks/
_STACK_FULL_SCOPES = [
    "stack:read",
    "stack:write",
    "stack:import",
    "stack:export",
    "stack:encrypt",
    "stack:decrypt",
    "stack:rename",
    "stack:delete",
    "stack:transfer",
    "stack:cancel_update",
    "stack_access:read",
    "stack_access:update",
    "stack_deployment:read",
    "stack_deployment:create",
    "stack_deployment_settings:write",
    "stack_deployment_settings:read",
    "stack_deployment_settings:encrypt",
    "stack_tags:update",
    "stack_schedule:create",
    "stack_schedule:update",
    "stack_schedule:read",
    "stack_schedule:delete",
    "stack_schedule:pause",
    "stack_schedule:resume",
]

# dev tier (read/write): day-to-day operator access — update stack state,
# import/export, manage encrypted config, queue deployments, tweak existing
# schedules — but no destructive ops (no rename/delete/transfer) and no
# access-grant management.
#
# Docs: https://www.pulumi.com/docs/administration/access-identity/rbac/scopes/stacks/
_STACK_WRITE_SCOPES = [
    "stack:read",
    "stack:write",
    "stack:import",
    "stack:export",
    "stack:encrypt",
    "stack:decrypt",
    "stack:cancel_update",
    "stack_deployment:read",
    "stack_deployment:create",
    "stack_deployment_settings:write",
    "stack_deployment_settings:read",
    "stack_deployment_settings:encrypt",
    "stack_tags:update",
    "stack_schedule:update",
    "stack_schedule:read",
    "stack_schedule:pause",
    "stack_schedule:resume",
]

# stage tier (read-only): inspect stack state, export checkpoints, decrypt
# secrets to view config, and read deployment/schedule metadata. No writes
# of any kind — useful for auditors and pre-prod gate reviewers.
#
# Docs: https://www.pulumi.com/docs/administration/access-identity/rbac/scopes/stacks/
_STACK_READ_SCOPES = [
    "stack:read",
    "stack:export",
    "stack:decrypt",
    "stack_deployment:read",
    "stack_deployment_settings:read",
    "stack_schedule:read",
]

# sbx tier (full): complete ESC environment ownership — read/open values,
# write the YAML, delete envs, clone, manage settings, full CRUD on tags
# and versions (including retract), and full CRUD/pause/resume on schedules.
#
# Docs:
# https://www.pulumi.com/docs/administration/access-identity/rbac/scopes/environments/
_ENV_FULL_SCOPES = [
    "environment:read",
    "environment:open",
    "environment:write",
    "environment:delete",
    "environment_settings:update",
    "environment:clone",
    "environment_tag:read",
    "environment_tag:create",
    "environment_tag:update",
    "environment_tag:delete",
    "environment_version:create",
    "environment_version:read",
    "environment_version:update",
    "environment_version:delete",
    "environment_version:open",
    "environment_version:retract",
    "environment_schedule:create",
    "environment_schedule:read",
    "environment_schedule:update",
    "environment_schedule:delete",
    "environment_schedule:pause",
    "environment_schedule:resume",
]

# dev tier (read/write): operator-level ESC access — read/open and write
# the YAML, clone, update existing tags and versions, and pause/resume/
# update existing schedules. No deletes (env, tag, version, or schedule)
# and no settings changes.
#
# Docs:
# https://www.pulumi.com/docs/administration/access-identity/rbac/scopes/environments/
_ENV_WRITE_SCOPES = [
    "environment:read",
    "environment:open",
    "environment:write",
    "environment:clone",
    "environment_tag:read",
    "environment_tag:update",
    "environment_version:read",
    "environment_version:update",
    "environment_version:open",
    "environment_schedule:read",
    "environment_schedule:update",
    "environment_schedule:pause",
    "environment_schedule:resume",
]

# stage tier (read-only): inspect ESC values — read/open the env, read its
# tags, read/open historical versions, and read schedule definitions. No
# mutations of any kind. Same scope set also gates the `shared=true` rule
# in the policy.
#
# Docs:
# https://www.pulumi.com/docs/administration/access-identity/rbac/scopes/environments/
_ENV_READ_SCOPES = [
    "environment:read",
    "environment:open",
    "environment_tag:read",
    "environment_version:read",
    "environment_version:open",
    "environment_schedule:read",
]


# Per-conditional-env mapping of which stack and env set to compose in.
# sbx/dev/stage → full/write/read for both stack and env contexts.
_CONDITIONAL_SETS: dict[str, tuple[str, str]] = {
    "sbx": ("stack_full", "env_full"),
    "dev": ("stack_write", "env_write"),
    "stage": ("stack_read", "env_read"),
}


# ----------------------------------------------------------------------------
# Wire-format descriptor builders
#
# Small constructors for the Pulumi Cloud permission-descriptor wire
# grammar (`__type`-discriminated maps). These functions translate the
# scope catalogs and environment mappings above into the API's expected
# format so the team / env tags wire up to the right permission sets.
# ----------------------------------------------------------------------------

ENV_CONTEXT: Mapping[str, Any] = {"__type": "PermissionExpressionEnvironment"}
STACK_CONTEXT: Mapping[str, Any] = {"__type": "PermissionExpressionStack"}


def _allow(scopes: list[str]) -> Mapping[str, Any]:
    return {"__type": "PermissionDescriptorAllow", "permissions": scopes}


def _compose(role_ids: list[str]) -> Mapping[str, Any]:
    return {
        "__type": "PermissionDescriptorCompose",
        "permissionDescriptors": role_ids,
    }


def _string_literal(value: str) -> Mapping[str, Any]:
    return {"__type": "PermissionLiteralExpressionString", "value": value}


def _tag_value(context: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    return {
        "__type": "PermissionExpressionTag",
        "context": context,
        "key": key,
    }


def _equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"__type": "PermissionExpressionEqual", "left": left, "right": right}


def _and(left: Mapping[str, Any], right: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"__type": "PermissionExpressionAnd", "left": left, "right": right}


def _team_env_condition(
    context: Mapping[str, Any], env_value: str
) -> Mapping[str, Any]:
    """team == <team_name> AND env == <env_value> for entities matching
    `context` (stack or ESC env)."""
    return _and(
        _equal(_tag_value(context, TEAM_TAG_KEY), _string_literal(team_name)),
        _equal(_tag_value(context, ENV_TAG_KEY), _string_literal(env_value)),
    )


def _conditional(
    condition: Mapping[str, Any], sub_node: Mapping[str, Any]
) -> Mapping[str, Any]:
    return {
        "__type": "PermissionDescriptorCondition",
        "condition": condition,
        "subNode": sub_node,
    }


# ----------------------------------------------------------------------------
# Resources
# ----------------------------------------------------------------------------

team = Team(
    "team",
    organization_name=organization_name,
    name=team_name,
    team_type="pulumi",
    display_name=team_name.capitalize(),
    description=f"Owns the {team_name}-domain applications across sbx/dev/stage/prod.",
)

# ESC envs (referenced as `<team_name>/<env>`).
environments: dict[str, Environment] = {}
for env_value in DEPLOYMENT_ENVS:
    environments[env_value] = Environment(
        f"env-{project_name}-{env_value}",
        organization=organization_name,
        project=project_name,
        name=env_value,
        yaml=pulumi.StringAsset(
            f'values:\n  placeholder: "{project_name}/{env_value} environment"\n'
        ),
    )

for env_value, environment in environments.items():
    EnvironmentTag(
        f"env-{project_name}-{env_value}-tag-team",
        org_name=organization_name,
        project_name=project_name,
        env_name=environment.name,
        name=TEAM_TAG_KEY,
        value=team_name,
    )
    EnvironmentTag(
        f"env-{project_name}-{env_value}-tag-env",
        org_name=organization_name,
        project_name=project_name,
        env_name=environment.name,
        name=ENV_TAG_KEY,
        value=env_value,
    )


# --- Permission sets (uxPurpose=set) ----------------------------------------
#
# Each set is a top-level role-like entity that the policy below references
# by ID via `PermissionDescriptorCompose`. Kept as separate resources so the
# UI surfaces them as named permission sets rather than inlined allows.

# (name, scopes, resource_type) per set. The Pulumi Cloud API requires
# a resource type on every permission set; it scopes which entity kind
# (global/stack/environment/insights-account) the scopes apply to.
_set_definitions: dict[str, tuple[str, list[str], str]] = {
    "base": (f"{team_name}-app-base", _BASE_SCOPES, "global"),
    "stack_full": (f"{team_name}-app-stack-full", _STACK_FULL_SCOPES, "stack"),
    "stack_write": (f"{team_name}-app-stack-write", _STACK_WRITE_SCOPES, "stack"),
    "stack_read": (f"{team_name}-app-stack-read", _STACK_READ_SCOPES, "stack"),
    "env_full": (f"{team_name}-app-env-full", _ENV_FULL_SCOPES, "environment"),
    "env_write": (f"{team_name}-app-env-write", _ENV_WRITE_SCOPES, "environment"),
    "env_read": (f"{team_name}-app-env-read", _ENV_READ_SCOPES, "environment"),
}

permission_sets: dict[str, ps_api.Role] = {}

for key, (set_name, scopes, resource_type) in _set_definitions.items():
    permission_sets[key] = ps_api.Role(
        f"perm-set-{key.replace('_', '-')}",
        org_name=organization_name,
        name=set_name,
        description=f"Permission set: {set_name}.",
        ux_purpose="set",
        resource_type=resource_type,
        details=_allow(scopes),
    )


# --- Policy (uxPurpose=policy) ----------------------------------------------
#
# `PermissionDescriptorGroup` of conditional Composes. The set IDs are
# Pulumi Outputs, so we assemble the descriptor inside an apply().


def _build_policy_details(ids: dict[str, str]) -> Mapping[str, Any]:
    entries: list[Mapping[str, Any]] = [_compose([ids["base"]])]

    # Stack-context tag rules per regulated env.
    for env_value in ROLE_CONDITIONAL_ENVS:
        stack_set_key, _ = _CONDITIONAL_SETS[env_value]
        entries.append(
            _conditional(
                _team_env_condition(STACK_CONTEXT, env_value),
                _compose([ids[stack_set_key]]),
            )
        )

    # ESC-env-context tag rules per regulated env.
    for env_value in ROLE_CONDITIONAL_ENVS:
        _, env_set_key = _CONDITIONAL_SETS[env_value]
        entries.append(
            _conditional(
                _team_env_condition(ENV_CONTEXT, env_value),
                _compose([ids[env_set_key]]),
            )
        )

    # Shared-env override: any ESC env tagged `shared=true` gets env-read.
    entries.append(
        _conditional(
            _equal(
                _tag_value(ENV_CONTEXT, SHARED_TAG_KEY),
                _string_literal("true"),
            ),
            _compose([ids["env_read"]]),
        )
    )

    return {"__type": "PermissionDescriptorGroup", "entries": entries}


policy_details = pulumi.Output.all(
    **{key: ps.role_id for key, ps in permission_sets.items()}
).apply(_build_policy_details)

team_policy = ps_api.Role(
    "team-policy",
    org_name=organization_name,
    name=f"{team_name}-app",
    description=f"{team_name} team policy: tag-conditional permission sets.",
    ux_purpose="policy",
    details=policy_details,
)


# --- Role (uxPurpose=role) --------------------------------------------------
#
# Single `PermissionDescriptorCompose` referencing the policy above.

role_details = team_policy.role_id.apply(lambda pid: _compose([pid]))

team_role = ps_api.Role(
    "team-role",
    org_name=organization_name,
    name=f"{team_name}-app",
    description=f"{team_name} app: composes the {team_name}-app policy.",
    ux_purpose="role",
    details=role_details,
)


# --- Team binding -----------------------------------------------------------

team_role_binding = TeamRoleAssignment(
    "team-role-binding",
    organization_name=organization_name,
    team_name=team.name,
    role_id=team_role.role_id,
)


# ----------------------------------------------------------------------------
# Stack outputs
# ----------------------------------------------------------------------------

# Outputs are emitted as flat snake_case keys (e.g. `role_name`,
# `permission_set_base_id`, `environment_sbx_name`) so downstream stacks
# can read them via `stack_ref.get_output("role_name")` without an
# .apply() to descend into nested dicts. Pulumi Cloud sorts them
# alphabetically, which keeps related keys grouped visually.

pulumi.export("team_name", team.name)

pulumi.export("role_name", team_role.name)
pulumi.export("role_id", team_role.role_id)

pulumi.export("policy_name", team_policy.name)
pulumi.export("policy_id", team_policy.role_id)

for _key, _ps in permission_sets.items():
    pulumi.export(f"permission_set_{_key}_name", _ps.name)
    pulumi.export(f"permission_set_{_key}_id", _ps.role_id)

for _env_value, _env in environments.items():
    pulumi.export(
        f"environment_{_env_value}_name",
        f"{organization_name}/{project_name}/{_env_value}",
    )
    pulumi.export(f"environment_{_env_value}_id", _env.environment_id)
