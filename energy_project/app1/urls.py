from django.urls import path
from . import views  # Ensure you're importing views properly
from .views import SensorReadingAPIView

urlpatterns = [
    path('', views.energy_dashboard, name='energy_dashboard'),
    path("readings/", SensorReadingAPIView.as_view(), name="sensor-readings"),
    
    
]