from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q

from backbone.access import audit, has_permission
from backbone.http import Problem, body, endpoint, identifier, string
from backbone.services.inbox import approval_outcome, close_task, open_task
from backbone.models import Assignment, Delegation, ModuleState, Role
from backbone.review_access import review_authority as scoped_review_authority
from backbone.review_access import reviewer_scopes as scoped_reviewer_scopes
from .models import ApprovalDecision, ApprovalRun, ApprovalTemplate, WorkOrder


def reviewer_scopes(user):
    return scoped_reviewer_scopes(user, "workorders")


def review_authority(user, run, scopes):
    if run.outcome != ApprovalRun.Outcome.PENDING:
        return False, None
    return scoped_review_authority(user, run.submitted_by_id, run.template.steps[run.next_step]["id"],
                                   run.order.site_id, scopes)


def can_review(user, run, scopes):
    return review_authority(user, run, scopes)[0]


def audit_approval(user, action, run, grant=None):
    target = f"WO-{run.order_id} / v{run.template.version}"
    if grant:
        target += f" / delegation #{grant.pk}"
    audit(user, f"workorder.approval.{action}", target)


def start_approval(user, order):
    template = ApprovalTemplate.objects.order_by("-version").first()
    if template is None:
        return WorkOrder.Status.SUBMITTED
    run = ApprovalRun.objects.create(order=order, template=template, submitted_by=user)
    open_task("wo", order.pk, f"{run.pk}:0", template.steps[0]["id"])
    audit_approval(user, "start", run)
    return WorkOrder.Status.IN_REVIEW


def review_routing(run):
    role = run.template.steps[run.next_step]
    site_scope = Q(site_id=run.order.site_id) | Q(site__isnull=True)
    assigned = Assignment.objects.filter(site_scope, role_id=role["id"]).values("user_id")
    delegated = Delegation.objects.filter(site_scope, role_id=role["id"],
                                           status=Delegation.Status.APPROVED).values("delegate_id")
    candidates = get_user_model().objects.filter(Q(pk__in=assigned) | Q(pk__in=delegated), is_active=True)
    return {
        "role_id": role["id"],
        "role_name": role["name"],
        "eligible_reviewers": [
            candidate.get_username() for candidate in candidates.order_by("username")
            if has_permission(candidate, "workorders.view", run.order.site_id)
            and can_review(candidate, run, reviewer_scopes(candidate))
        ],
    }


def review_details(user, order):
    runs = list(order.approval_runs.select_related("template", "submitted_by")
                .prefetch_related("decisions").order_by("-id"))
    scopes = reviewer_scopes(user) if order.status == WorkOrder.Status.IN_REVIEW else (set(), [])
    details = {
        "can_review": bool(runs and can_review(user, runs[0], scopes)),
        "approval_runs": [
            {
                "version": run.template.version, "outcome": run.outcome,
                "submitted_by": run.submitted_by.get_username(), "submitted_at": run.submitted_at.isoformat(),
                "steps": [step["name"] for step in run.template.steps], "next_step": run.next_step,
                "decisions": [
                    {"step": item.step, "actor": item.actor, "action": item.action,
                     "reason": item.reason, "decided_at": item.decided_at.isoformat(),
                     "delegation_id": item.delegation_id}
                    for item in sorted(run.decisions.all(), key=lambda item: item.step)
                ],
            }
            for run in runs
        ],
    }
    if user.is_superuser and order.status == WorkOrder.Status.IN_REVIEW and runs:
        if runs[0].outcome == ApprovalRun.Outcome.PENDING:
            details["review_routing"] = review_routing(runs[0])
    return details


def apply_decision(user, order, data):
    if identifier(data.get("revision"), "revision") != order.revision:
        raise Problem("revision_conflict", 409)
    if order.status != WorkOrder.Status.IN_REVIEW:
        raise Problem("not_in_review", 409)
    run = order.approval_runs.select_related("template").get(outcome=ApprovalRun.Outcome.PENDING)
    allowed, grant = review_authority(user, run, reviewer_scopes(user))
    if not allowed:
        raise Problem("forbidden", 403)

    action = data.get("action")
    if action not in ("approve", "return"):
        raise Problem("invalid_approval_action")
    reason = string(data, "reason", 1000, required=action == "return")
    step = run.next_step
    close_task("wo", order.pk, f"{run.pk}:{step}")
    ApprovalDecision.objects.create(run=run, step=step, actor=user.get_username(),
                                    action=action, reason=reason, delegation=grant)
    if action == "return":
        run.outcome = ApprovalRun.Outcome.RETURNED
        order.status = WorkOrder.Status.DRAFT
        order.submitted_at = None
    else:
        run.next_step += 1
        if run.next_step == len(run.template.steps):
            run.outcome = ApprovalRun.Outcome.APPROVED
            order.status = WorkOrder.Status.APPROVED

    run.save(update_fields=["next_step", "outcome"])
    order.revision += 1
    order.save(update_fields=["status", "submitted_at", "revision"])
    if run.outcome == ApprovalRun.Outcome.PENDING:
        open_task("wo", order.pk, f"{run.pk}:{run.next_step}", run.template.steps[run.next_step]["id"])
    else:
        approval_outcome(run.submitted_by_id, "wo", order.pk, run.pk, step, action)
    audit_approval(user, action, run, grant)


@endpoint("GET", "POST", module="workorders", admin=True)
def templates(request):
    if request.method == "GET":
        versions = ApprovalTemplate.objects.order_by("-version")
        roles = [role for role in Role.objects.order_by("name").values("id", "name", "permissions")
                 if {"workorders.view", "workorders.approve"}.issubset(role["permissions"])]
        return {"versions": list(versions.values("version", "steps", "published_at")), "roles": roles}

    selected = body(request).get("role_ids")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 10:
        raise Problem("invalid_approval_steps")
    role_ids = [identifier(value, "approval_steps") for value in selected]
    if len(set(role_ids)) != len(role_ids):
        raise Problem("invalid_approval_steps")
    roles = Role.objects.in_bulk(role_ids)
    if any(role not in roles or not {"workorders.view", "workorders.approve"}.issubset(roles[role].permissions)
           for role in role_ids):
        raise Problem("invalid_approval_steps")
    with transaction.atomic():
        ModuleState.objects.select_for_update().get(pk="workorders")
        previous = ApprovalTemplate.objects.order_by("-version").first()
        steps = [{"id": role, "name": roles[role].name} for role in role_ids]
        template = ApprovalTemplate.objects.create(version=previous.version + 1 if previous else 1, steps=steps)
        audit(request.user, "workorder.approval.publish", f"v{template.version}")
    return {"version": template.version, "steps": steps, "published_at": template.published_at.isoformat()}, 201
