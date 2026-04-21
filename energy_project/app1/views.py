from django.shortcuts import render

# Create your views here.

def energy_dashboard(request):
    return render(request, 'app1/energy_dashboard.html')