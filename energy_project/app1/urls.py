from django.urls import path
from . import views  # Ensure you're importing views properly
from .views import SensorReadingAPIView

urlpatterns = [
    # Frontend
    path('', views.energy_dashboard, name='energy_dashboard'),
    
    # API endpoints
    path('readings/', views.SensorReadingAPIView.as_view(), name='sensor-readings'),
    path('api/readings/latest/', views.LatestReadingView.as_view(), name='latest-reading'),
    path('api/readings/summary/', views.SummaryStatsView.as_view(), name='summary-stats'),
    path('api/readings/graph/', views.GraphDataView.as_view(), name='graph-data'),
    path('api/readings/recent/', views.RecentReadingsView.as_view(), name='recent-readings'),
]