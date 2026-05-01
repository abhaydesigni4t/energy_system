from django.shortcuts import render

# Create your views here.

from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Avg, Max, Min, Sum
from django.utils import timezone
from datetime import datetime, timedelta
import logging

from .models import SensorReading, HourlyAggregate
from .serializers import SensorReadingSerializer, LatestReadingSerializer, SummaryStatsSerializer, GraphDataSerializer

logger = logging.getLogger(__name__)

def energy_dashboard(request):
    """Serve the energy dashboard HTML"""
    return render(request, 'app1/energy_dashboard.html')

class SensorReadingAPIView(APIView):
    """
    POST: Receive and store Modbus readings
    GET: Retrieve readings (with optional filtering)
    """
    
    def post(self, request):
        """Store a new reading from the SBC"""
        try:
            # Validate required fields
            required_fields = ['unit_id', 'timestamp', 'block1_raw', 'block2_raw']
            for field in required_fields:
                if field not in request.data:
                    return Response(
                        {"error": f"Missing required field: {field}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Prepare data for serializer
            data = {
                'unit_id': request.data.get('unit_id'),
                'timestamp': request.data.get('timestamp'),
                'block1_start_reg': request.data.get('block1_start_reg', 40100),
                'block1_raw': request.data.get('block1_raw'),
                'block2_start_reg': request.data.get('block2_start_reg', 40138),
                'block2_raw': request.data.get('block2_raw'),
            }
            
            serializer = SensorReadingSerializer(data=data)
            if serializer.is_valid():
                reading = serializer.save()
                
                # Trigger hourly aggregation update
                self.update_hourly_aggregate(reading)
                
                return Response({
                    "status": "success",
                    "message": "Reading stored successfully",
                    "id": reading.id
                }, status=status.HTTP_201_CREATED)
            
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        except Exception as e:
            logger.error(f"Error storing reading: {str(e)}")
            return Response(
                {"error": f"Internal server error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def update_hourly_aggregate(self, reading):
        """Update or create hourly aggregate for this reading's hour"""
        try:
            # Convert unix timestamp to datetime
            reading_time = datetime.fromtimestamp(reading.timestamp)
            hour_start = reading_time.replace(minute=0, second=0, microsecond=0)
            
            # Get all readings in this hour
            hour_end = hour_start + timedelta(hours=1)
            hour_readings = SensorReading.objects.filter(
                unit_id=reading.unit_id,
                timestamp__gte=hour_start.timestamp(),
                timestamp__lt=hour_end.timestamp()
            )
            
            if hour_readings.exists():
                # Calculate aggregates
                avg_power = hour_readings.aggregate(Avg('active_power_kw'))['active_power_kw__avg'] or 0
                min_power = hour_readings.aggregate(Min('active_power_kw'))['active_power_kw__min'] or 0
                max_power = hour_readings.aggregate(Max('active_power_kw'))['active_power_kw__max'] or 0
                count = hour_readings.count()
                
                # Energy (kWh) = average power (kW) * 1 hour
                total_energy = avg_power * 1.0
                
                # Update or create aggregate
                HourlyAggregate.objects.update_or_create(
                    unit_id=reading.unit_id,
                    hour=hour_start,
                    defaults={
                        'avg_active_power_kw': avg_power,
                        'min_active_power_kw': min_power,
                        'max_active_power_kw': max_power,
                        'total_energy_kwh': total_energy,
                        'reading_count': count,
                    }
                )
        except Exception as e:
            logger.error(f"Error updating hourly aggregate: {str(e)}")
    
    def get(self, request):
        """Retrieve readings (for debugging)"""
        unit_id = request.query_params.get('unit_id')
        limit = int(request.query_params.get('limit', 100))
        
        queryset = SensorReading.objects.all()
        if unit_id:
            queryset = queryset.filter(unit_id=unit_id)
        
        queryset = queryset[:limit]
        serializer = SensorReadingSerializer(queryset, many=True)
        return Response(serializer.data)

class LatestReadingView(APIView):
    """Get the latest reading for live dashboard updates"""
    
    def get(self, request):
        unit_id = request.query_params.get('unit_id', 100)
        
        try:
            # Get the most recent reading
            latest = SensorReading.objects.filter(unit_id=unit_id).latest('timestamp')
            
            # Check if data is stale (older than 30 seconds)
            current_time = timezone.now().timestamp()
            is_stale = (current_time - latest.timestamp) > 30
            
            data = {
                'voltage': round(latest.voltage, 1),
                'current': round(latest.current, 1),
                'active_power_kw': round(latest.active_power_kw, 1),
                'reactive_power_kvar': round(latest.reactive_power_kvar, 1),
                'apparent_power_kva': round(latest.apparent_power_kva, 1),
                'energy_today_kwh': round(latest.energy_today_kwh, 1),
                'timestamp': datetime.fromtimestamp(latest.timestamp).isoformat(),
                'status': 'stale' if is_stale else 'online'
            }
            
            serializer = LatestReadingSerializer(data)
            return Response(serializer.data)
            
        except SensorReading.DoesNotExist:
            return Response({
                'status': 'offline',
                'message': 'No readings available'
            }, status=status.HTTP_404_NOT_FOUND)

class SummaryStatsView(APIView):
    """Get summary statistics for today, week, and month"""
    
    def get(self, request):
        unit_id = request.query_params.get('unit_id', 100)
        now = timezone.now()
        
        # Get the latest reading to know current energy value
        try:
            latest = SensorReading.objects.filter(unit_id=unit_id).latest('timestamp')
            current_energy = latest.energy_today_kwh
        except SensorReading.DoesNotExist:
            current_energy = 0
        
        def get_energy_at_time(target_time):
            """Get energy reading closest to a specific time"""
            # Convert target_time to timestamp
            target_ts = target_time.timestamp()
            
            # Find reading just before or at target time
            reading = SensorReading.objects.filter(
                unit_id=unit_id,
                timestamp__lte=target_ts
            ).order_by('-timestamp').first()
            
            return reading.energy_today_kwh if reading else current_energy
        
        # Calculate start times
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=7)
        month_start = today_start - timedelta(days=30)
        
        # Get energy at start of each period
        energy_today_start = get_energy_at_time(today_start)
        energy_week_start = get_energy_at_time(week_start)
        energy_month_start = get_energy_at_time(month_start)
        
        # Calculate consumption
        today_kwh = max(0, current_energy - energy_today_start)
        week_kwh = max(0, current_energy - energy_week_start)
        month_kwh = max(0, current_energy - energy_month_start)
        
        # Get peak demand for today
        peak_demand = SensorReading.objects.filter(
            unit_id=unit_id,
            timestamp__gte=today_start.timestamp()
        ).aggregate(Max('active_power_kw'))['active_power_kw__max'] or 0
        
        data = {
            'today_kwh': round(today_kwh, 1),
            'week_kwh': round(week_kwh, 1),
            'month_kwh': round(month_kwh, 1),
            'peak_demand_kw': round(peak_demand, 1)
        }
        
        serializer = SummaryStatsSerializer(data)
        return Response(serializer.data)

class GraphDataView(APIView):
    """Get data for consumption trend graph - NOW WITH RAW DATA OPTION"""
    
    def get(self, request):
        unit_id = request.query_params.get('unit_id', 100)
        period = request.query_params.get('period', 'today')
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        
        now = timezone.now()
        
        # Determine date range
        if from_date and to_date:
            try:
                start = datetime.fromisoformat(from_date)
                end = datetime.fromisoformat(to_date)
                # Add one day to include the end date
                end = end + timedelta(days=1)
            except ValueError:
                return Response({"error": "Invalid date format"}, status=400)
        elif period == 'today':
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif period == 'week':
            start = now - timedelta(days=7)
            end = now
        else:  # month
            start = now - timedelta(days=30)
            end = now
        
        start_ts = start.timestamp()
        end_ts = end.timestamp()
        
        # FIX: ALWAYS get raw readings first for today/week view
        # Only use aggregates for month view or large datasets
        readings = SensorReading.objects.filter(
            unit_id=unit_id,
            timestamp__gte=start_ts,
            timestamp__lte=end_ts
        ).order_by('timestamp')
        
        if readings.exists():
            # For better visibility, show individual readings for small datasets
            if period == 'today' or readings.count() <= 48:  # Show individual readings for today or small datasets
                labels = []
                values = []
                
                for reading in readings:
                    dt = datetime.fromtimestamp(reading.timestamp)
                    # Format: HH:MM for today, MM-DD HH:MM for others
                    if period == 'today':
                        labels.append(dt.strftime('%H:%M'))
                    else:
                        labels.append(dt.strftime('%m-%d %H:%M'))
                    
                    # Get the power value
                    power_value = reading.active_power_kw if reading.active_power_kw else 0
                    values.append(round(power_value, 2))
                
                data = {
                    'labels': labels,
                    'values': values,
                    'unit': 'kW',
                    'type': 'raw_readings',
                    'count': len(labels)
                }
            else:
                # For large datasets, use hourly aggregation
                hourly_data = {}
                for reading in readings:
                    hour_key = datetime.fromtimestamp(reading.timestamp).replace(minute=0, second=0, microsecond=0)
                    if hour_key not in hourly_data:
                        hourly_data[hour_key] = {'total': 0, 'count': 0}
                    if reading.active_power_kw:
                        hourly_data[hour_key]['total'] += reading.active_power_kw
                        hourly_data[hour_key]['count'] += 1
                
                labels = []
                values = []
                for hour_key in sorted(hourly_data.keys()):
                    if period == 'today':
                        labels.append(hour_key.strftime('%H:00'))
                    else:
                        labels.append(hour_key.strftime('%m-%d %H:00'))
                    avg_value = hourly_data[hour_key]['total'] / hourly_data[hour_key]['count']
                    values.append(round(avg_value, 2))
                
                data = {
                    'labels': labels,
                    'values': values,
                    'unit': 'kW',
                    'type': 'hourly_aggregated',
                    'count': len(labels)
                }
        else:
            data = {
                'labels': [],
                'values': [],
                'unit': 'kW',
                'type': 'no_data',
                'count': 0
            }
        
        return Response(data)

class RecentReadingsView(APIView):
    """Get recent readings for the table"""
    
    def get(self, request):
        unit_id = request.query_params.get('unit_id', 100)
        limit = int(request.query_params.get('limit', 20))
        
        readings = SensorReading.objects.filter(unit_id=unit_id)[:limit]
        
        data = []
        for reading in readings:
            dt = datetime.fromtimestamp(reading.timestamp)
            data.append({
                'time': dt.strftime('%H:%M:%S'),
                'date': dt.strftime('%Y-%m-%d'),
                'kw': round(reading.active_power_kw, 2) if reading.active_power_kw else 0,
                'voltage': round(reading.voltage, 1) if reading.voltage else 0,
                'current': round(reading.current, 1) if reading.current else 0,
                'energy': round(reading.energy_today_kwh, 1) if reading.energy_today_kwh else 0,
                'pf': round(reading.active_power_kw / reading.apparent_power_kva, 2) if reading.apparent_power_kva and reading.apparent_power_kva > 0 else 0,
                'status': 'ok'
            })
        
        return Response(data)