from django.urls import path

from . import approval_views, views

urlpatterns = [
    path("approvals/", approval_views.approvals, name="approvals"),
    path("approvals/status/<slug:status>/", approval_views.approvals, name="approval_status"),
    path("approvals/new/", approval_views.approval_new, name="approval_upload"),
    path("approvals/new/<int:pk>/", approval_views.approval_new, name="approval_new"),
    path("approvals/<int:pk>/", approval_views.approval_detail, name="approval_detail"),
    path("approvals/file/<int:pk>/", approval_views.approval_file, name="approval_file"),
    path("approvals/file/<int:pk>/preview/", approval_views.approval_file, {"preview": True}, name="approval_preview"),
    path("", views.workspace, name="workspace"),
    path("archive/", views.archive, name="archive"),
    path("archive/<int:pk>/", views.document_detail, name="document_detail"),
    path("archive/<int:pk>/version/", views.add_version, name="add_version"),
    path("archive/<int:pk>/delete/", views.delete, name="delete_document"),
    path("archive/<int:pk>/delete/<int:version_pk>/", views.delete, name="delete_version"),
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
