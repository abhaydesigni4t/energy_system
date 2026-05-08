from django.urls import path
from . import views  # Ensure you're importing views properly
from .views import SensorReadingAPIView

urlpatterns = [
    # Frontend
    # ── Authentication ──────────────────────────────────────────
    path('',    views.login_view,   name='login'),
    path('signup/',   views.signup_view,  name='signup'),
    path('logout/',   views.logout_view,  name='logout'),
 
    # ── Dashboard ───────────────────────────────────────────────
    path('dashboard/', views.energy_dashboard, name='energy_dashboard'),
    path('dashboard/v2/', views.energy_dashboard_v2, name='energy_dashboard_v2'),
    
    # ── User Management (Admin only) ────────────────────────────
    path('users/',                        views.user_list,    name='user_list'),
    path('users/add/',                    views.add_user,     name='add_user'),
    path('users/<int:user_id>/',          views.user_detail,  name='user_detail'),
    path('users/<int:user_id>/assign/',   views.assign_units, name='assign_units'),
    path('users/<int:user_id>/delete/',   views.delete_user,  name='delete_user'),
    
    # API endpoints
    path('readings/', views.SensorReadingAPIView.as_view(), name='sensor-readings'),
    path('api/readings/latest/', views.LatestReadingView.as_view(), name='latest-reading'),
    path('api/readings/summary/', views.SummaryStatsView.as_view(), name='summary-stats'),
    path('api/readings/graph/', views.GraphDataView.as_view(), name='graph-data'),
    path('api/readings/recent/', views.RecentReadingsView.as_view(), name='recent-readings'),
    
    # ── Device Management ────────────────────────────────────────
    path('device-management/', views.company_list, name='company_list'),
    path('device-management/companies/add/', views.add_company, name='add_company'),
    path('device-management/companies/<int:company_id>/', views.company_detail, name='company_detail'),
    path('device-management/companies/<int:company_id>/edit/', views.edit_company, name='edit_company'),
    path('device-management/companies/<int:company_id>/delete/', views.delete_company, name='delete_company'),
 
    # ── Devices ──────────────────────────────────────────────────
    path('device-management/devices/', views.device_list, name='device_list'),
    path('device-management/devices/add/', views.add_device, name='add_device'),
    path('device-management/devices/<int:device_id>/', views.device_detail, name='device_detail'),
    path('device-management/devices/<int:device_id>/edit/', views.edit_device, name='edit_device'),
    path('device-management/devices/<int:device_id>/delete/', views.delete_device, name='delete_device'),
]