from django.urls import path

from . import views, workflow_views

urlpatterns = [
    path("workflow/", workflow_views.workflow_records, name="workflow_records"),
    path("workflow/new/", workflow_views.workflow_create, name="workflow_upload"),
    path("workflow/new/<int:pk>/", workflow_views.workflow_create, name="workflow_create"),
    path("workflow/<int:pk>/edit/", workflow_views.workflow_edit, name="workflow_edit"),
    path("workflow/<int:pk>/comments/", workflow_views.workflow_comment, name="workflow_comment"),
    path("workflow/<int:pk>/events/<int:event_pk>/file/", workflow_views.workflow_event_file, name="workflow_event_file"),
    path("workflow/<int:pk>/file/", workflow_views.workflow_file, name="workflow_file"),
    path("workflow/<int:pk>/", workflow_views.workflow_detail, name="workflow_detail"),
    path("", views.workspace, name="workspace"),
    path("history/", views.history, name="history"),
    path("history/<int:pk>/", views.batch_detail, name="batch_detail"),
    path("history/<int:pk>/zip/", views.download_batch, name="download_batch"),
    path("api/batch/", views.new_batch, name="new_batch"),
    path("api/batch/<int:pk>/upload/", views.upload, name="upload"),
    path("api/batch/<int:pk>/submit/", views.submit, name="submit"),
    path("api/compression-preview/", views.new_compression_preview, name="new_compression_preview"),
    path("api/compression-preview/<uuid:pk>/", views.compression_preview, name="compression_preview"),
    path("job/<int:pk>/retry/", views.retry, name="retry"),
    path("file/original/<int:pk>/preview/", views.download_version, {"preview": True}, name="preview_version"),
    path("file/original/<int:pk>/", views.download_version, name="download_version"),
    path("file/result/<int:pk>/preview/", views.download_result, {"preview": True}, name="preview_result"),
    path("file/result/<int:pk>/", views.download_result, name="download_result"),
]
