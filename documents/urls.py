from django.urls import path

from . import task_documents, views, workflow_views

urlpatterns = [
    path("notifications/", workflow_views.notifications, name="notifications"),
    path("tasks/", workflow_views.task_list, name="task_list"),
    path("tasks/incoming/", workflow_views.task_list, {"folder": "incoming"}, name="task_incoming"),
    path("tasks/sent/", workflow_views.task_list, {"folder": "sent"}, name="task_sent"),
    path("tasks/notifications/", workflow_views.task_notifications, name="task_notifications"),
    path("tasks/<int:pk>/compare/", task_documents.compare_versions, name="compare_versions"),
    path("file/result/<int:pk>/attach/", task_documents.attach_result, name="attach_result"),
    path("tasks/<int:pk>/delete/", workflow_views.workflow_delete, name="workflow_delete"),
    path("tasks/<int:pk>/events/<int:event_pk>/screenshots/<int:file_pk>/",
         workflow_views.comment_screenshot, name="comment_screenshot"),
    path("tasks/<int:pk>/comments/", workflow_views.workflow_comment, name="workflow_comment"),
    path(
        "tasks/<int:pk>/events/<int:event_pk>/preview/",
        workflow_views.workflow_file,
        {"preview": True},
        name="workflow_event_preview",
    ),
    path(
        "tasks/<int:pk>/preview/", workflow_views.workflow_file, {"preview": True}, name="workflow_preview"
    ),
    path(
        "tasks/<int:pk>/events/<int:event_pk>/file/",
        workflow_views.workflow_file,
        name="workflow_event_file",
    ),
    path("tasks/<int:pk>/file/", workflow_views.workflow_file, name="workflow_file"),
    path("tasks/<int:pk>/", workflow_views.workflow_detail, name="workflow_detail"),
    path("", views.workspace, name="workspace"),
    path("history/", views.history, name="history"),
    path("history/<int:pk>/", views.batch_detail, name="batch_detail"),
    path("history/<int:pk>/zip/", views.download_batch, name="download_batch"),
    path("api/batch/", views.new_batch, name="new_batch"),
    path("api/batch/<int:pk>/upload/", views.upload, name="upload"),
    path("api/batch/<int:pk>/submit/", views.submit, name="submit"),
    path("api/compression-preview/", views.new_compression_preview, name="new_compression_preview"),
    path("api/compression-preview/<uuid:pk>/", views.compression_preview, name="compression_preview"),
    path("job/<int:pk>/cancel/", views.cancel_job, name="cancel_job"),
    path("job/<int:pk>/retry/", views.retry, name="retry"),
    path(
        "file/original/<int:pk>/preview/", views.download_version, {"preview": True}, name="preview_version"
    ),
    path("file/original/<int:pk>/", views.download_version, name="download_version"),
    path("file/result/<int:pk>/preview/", views.download_result, {"preview": True}, name="preview_result"),
    path("file/result/<int:pk>/", views.download_result, name="download_result"),
]
