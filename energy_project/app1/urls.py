from django.urls import path
from . import views  # Ensure you're importing views properly


urlpatterns = [
    path('', views.energy_dashboard, name='energy_dashboard'),
    
    
    
]