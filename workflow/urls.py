from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("projects/", views.project_list, name="project_list"),
    path("cases/new/", views.case_create, name="case_create"),
    path("cases/<int:pk>/", views.case_detail, name="case_detail"),
    path("cases/<int:pk>/edit/", views.case_edit, name="case_edit"),
    path("cases/<int:pk>/upload/", views.document_upload, name="document_upload"),
    path("cases/<int:pk>/do/<str:action>/", views.case_action, name="case_action"),
    path("documents/<int:pk>/download/", views.document_download, name="document_download"),
    path("documents/<int:pk>/view/", views.document_inline, name="document_inline"),
    path("documents/<int:pk>/comments/", views.document_comment, name="document_comment"),
    path("documents/<int:pk>/decide/", views.document_decide, name="document_decide"),
    path("signatures/<int:pk>/image/", views.signature_image, name="signature_image"),
    path("signatures/<int:pk>/signed/", views.signed_download, name="signed_download"),
]
