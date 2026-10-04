from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("cases/new/", views.case_create, name="case_create"),
    path("cases/<int:pk>/", views.case_detail, name="case_detail"),
    path("cases/<int:pk>/edit/", views.case_edit, name="case_edit"),
    path("cases/<int:pk>/upload/", views.document_upload, name="document_upload"),
    path("cases/<int:pk>/do/<str:action>/", views.case_action, name="case_action"),
    path("documents/<int:pk>/download/", views.document_download, name="document_download"),
]
