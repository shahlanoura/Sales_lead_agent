from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("campaigns", views.CampaignViewSet, basename="campaign")
router.register("leads", views.LeadViewSet, basename="lead")
router.register("outreach", views.OutreachViewSet, basename="outreach")
router.register("followups", views.FollowUpViewSet, basename="followup")

urlpatterns = [
    path("auth/login/", obtain_auth_token),
    path("auth/logout/", views.logout),
    path("auth/profile/", views.profile),
    path("profile/", views.profile),
    path("analytics/overview/", views.analytics_overview),
    path("", include(router.urls)),
]
