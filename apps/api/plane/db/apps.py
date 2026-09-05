from django.apps import AppConfig


class DbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "plane.db"
    label = "db"

    def ready(self) -> None:
        """TASK-008 BR-13：字段定义 post_save/post_delete → Redis Schema 缓存主动失效。"""
        from plane.db.services.field_schema import register_signals

        register_signals()
