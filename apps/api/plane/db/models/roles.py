from django.db import models


class WorkspaceRole(models.IntegerChoices):
    OWNER = 20, "Owner"
    ADMIN = 15, "Admin"
    MEMBER = 10, "Member"
    GUEST = 5, "Guest"


class ProjectRole(models.IntegerChoices):
    ADMIN = 20, "Admin"
    CONTRIBUTOR = 15, "Contributor"
    COMMENTER = 10, "Commenter"
    VIEWER = 5, "Viewer"
