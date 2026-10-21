from django.urls import path
from . import views  # Ensure you're importing views properly
from .views import *
from django.conf import settings
from django.conf.urls.static import static


urlpatterns = [
    # Frontend
    # ── Authentication ──────────────────────────────────────────
    path('',          views.landing_view,   name='landing'),
    path('start/<str:option>/', views.select_option, name='select_option'),
    path('check-email/', views.check_email,  name='check_email'),
    path('login/', views.check_email, name='login'),
    path('signup/',   views.signup_view,  name='signup'),
    path('logout/',   views.logout_view,  name='logout'),
    path('verify-otp/', views.verify_otp, name='verify_otp'),
    path('resend-otp/', views.resend_otp, name='resend_otp'),
    path('use-different-email/', views.use_different_email, name='use_different_email'),
    path('welcome/design/', views.design_welcome, name='design_welcome'),
    path('welcome/commission/', views.commission_welcome, name='commission_welcome'),
    path('coming-soon/<str:option>/', views.coming_soon_view, name='coming_soon'),
    path('beacon-logout/', views.beacon_logout, name='beacon_logout'),
    path('keepalive/', views.keepalive, name='keepalive'),
    path('complete/',   views.energy_dashboard_complete,  name='complete'),
    # ── Dashboard ───────────────────────────────────────────────
    path('dashboard/', views.energy_dashboard, name='energy_dashboard'),
    path('dashboard/v2/', views.energy_dashboard_v2, name='energy_dashboard_v2'),
    path('dashboard/v2/<int:company_id>/', views.energy_dashboard_v2, name='energy_dashboard_v2_company'),
    path('home/',   views.home,  name='home'),
    path('home/<int:company_id>/',    views.home, name='home_company'),
    
    # ── User Management (Admin only) ────────────────────────────
    path('users/',                        views.user_list,    name='user_list'),
    path('users/add/',                    views.add_user,     name='add_user'),
    path('users/<int:user_id>/',          views.user_detail,  name='user_detail'),
    path('users/<int:user_id>/assign/',   views.assign_units, name='assign_units'),
    path('users/<int:user_id>/delete/',   views.delete_user,  name='delete_user'),
    path('users/<int:user_id>/edit/', views.edit_user, name='edit_user'),
    
    # API endpoints
    path('readings/', views.SensorReadingAPIView.as_view(), name='sensor-readings'),
    path('meter_readings/', MeterReadingAPIView.as_view(), name='meter-readings'),
    path('api/inverter-readings/post/', views.post_inverter_reading, name='post_inverter_reading'),
    path('api/inverter-readings/', views.get_inverter_readings, name='get_inverter_readings'),    
    path('api/readings/filter/', SensorReadingFilterView.as_view(), name='sensor-readings-filter'),
    path('api/readings/latest/', views.LatestReadingView.as_view(), name='latest-reading'),
    path('api/inverter/latest/', views.InverterLatestView.as_view(), name='inverter-latest'),
    path('api/inverter/recent/', views.InverterRecentView.as_view(), name='inverter-recent'),
    path('api/readings/summary/', views.SummaryStatsView.as_view(), name='summary-stats'),
    path('api/readings/graph/', views.GraphDataView.as_view(), name='graph-data'),
    path('api/readings/recent/', views.RecentReadingsView.as_view(), name='recent-readings'),
    path('api/devices/by-category/', views.DevicesByCategoryView.as_view(), name='devices_by_category'),
    path('api/readings/verify/', EnergyVerificationView.as_view(), name='energy-verify'),
    path('api/readings/revenue-verify/', RevenueVerificationView.as_view()),
    path('api/readings/period-energy/', PeriodEnergyView.as_view()),
    path('api/readings/period-revenue/', PeriodRevenueView.as_view()),
    
    path('customer-management/', views.customer_list, name='customer_list'),
    path('customer-management/add/', views.add_customer, name='add_customer'),
    path('customer-management/<int:customer_id>/', views.customer_detail, name='customer_detail'),
    path('customer-management/<int:customer_id>/edit/', views.edit_customer, name='edit_customer'),
    path('customer-management/<int:customer_id>/delete/', views.delete_customer, name='delete_customer'),
    
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
    path('device/<int:device_id>/bill/', views.select_bill_month, name='select_bill_month'),
    path('device/<int:device_id>/bill/download/', views.generate_bill, name='generate_bill'),
    path('device-management/bill-logo/', views.upload_bill_logo, name='upload_bill_logo'),
    path('gats_readings/', GatsMeterReadingView.as_view(), name='gats_meter_reading'),

]  

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
