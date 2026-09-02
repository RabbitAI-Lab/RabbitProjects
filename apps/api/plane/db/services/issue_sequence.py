"""advisory lock + MAX()+1 序列号生成（INFRA-003 §4.11）。"""

import uuid

from django.db import connection, transaction
from django.db.models import Max


def project_lock_key(project_id: uuid.UUID) -> int:
    return project_id.int >> 65


def acquire_project_lock(project_id: uuid.UUID) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [project_lock_key(project_id)])


def next_sequence_id(project_id: uuid.UUID) -> int:
    from plane.db.models import Issue

    current_max = Issue.all_objects.filter(project_id=project_id).aggregate(max_seq=Max("sequence_id"))["max_seq"]
    return (current_max or 0) + 1


@transaction.atomic
def create_issue(*, project_id: uuid.UUID, actor_id: uuid.UUID, payload: dict):
    from plane.db.models import Issue
    from plane.db.services.sort_order import calculate_sort_order

    acquire_project_lock(project_id)
    return Issue.objects.create(
        project_id=project_id,
        created_by_id=actor_id,
        sequence_id=next_sequence_id(project_id),
        sort_order=calculate_sort_order(prev_order=None, next_order=payload.get("next_sort_order")),
        **payload,
    )
