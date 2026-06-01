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
from .models import *
from .forms import *

from .models import SensorReading, HourlyAggregate
from .serializers import SensorReadingSerializer, LatestReadingSerializer, SummaryStatsSerializer, GraphDataSerializer

logger = logging.getLogger(__name__)

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from functools import wraps

from .models import User
from .forms import LoginForm, SignUpForm, AddUserForm, AssignUnitsForm
from zoneinfo import ZoneInfo   # Python 3.9+; or: from pytz import timezone as pytz_tz
EST = ZoneInfo("America/New_York")


# ── Decorators ──────────────────────────────────────────────────────────────

def admin_required(view_func):
    """Restrict view to admin / superadmin users."""
    @wraps(view_func)
    @login_required(login_url='login')
    def wrapper(request, *args, **kwargs):
        if not request.user.is_admin_or_superadmin():
            return HttpResponseForbidden(
                "<h2>403 – Forbidden</h2><p>Admin access required.</p>"
            )
        return view_func(request, *args, **kwargs)
    return wrapper


# ── Auth Views ───────────────────────────────────────────────────────────────

def login_view(request):
    """Sign In page"""
    if request.user.is_authenticated:
        return redirect('home')  # Changed from 'energy_dashboard_v2'

    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)
        messages.success(request, f'Welcome back, {user.first_name or user.username}!')
        next_url = request.GET.get('next', 'home')  # Changed default to 'home'
        return redirect(next_url)

    return render(request, 'app1/login.html', {'form': form})


def signup_view(request):
    """Sign Up page – creates a normal user"""
    if request.user.is_authenticated:
        return redirect('home')  # Changed from 'energy_dashboard_v2'

    form = SignUpForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, f'Account created! Welcome, {user.first_name or user.username}!')
        return redirect('home')  # Changed from 'energy_dashboard_v2'

    return render(request, 'app1/signup.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.info(request, 'You have been signed out.')
    return redirect('login')


def energy_dashboard_complete(request):
    return render(request,'app1/energy_dashboard_complete.html')

# ── Dashboard ────────────────────────────────────────────────────────────────

@login_required(login_url='login')
def energy_dashboard(request):
    """Energy Dashboard — redirect target after login"""
    # Determine which unit_ids the user can see
    if request.user.is_admin_or_superadmin():
        accessible_units = list(
            User.objects.values_list('unit_ids', flat=True)
        )
        # Flatten all unit lists
        all_units = set()
        for units in accessible_units:
            if units:
                all_units.update(units)
        # Default to 100 if none assigned
        all_units.add(100)
        unit_ids = sorted(all_units)
    else:
        unit_ids = request.user.get_unit_ids() or [100]

    context = {
        'user': request.user,
        'unit_ids': unit_ids,
        'primary_unit': unit_ids[0] if unit_ids else 100,
    }
    return render(request, 'app1/energy_dashboard.html', context)


# ── User Management (Admin only) ─────────────────────────────────────────────

@admin_required
def user_list(request):
    """List all users — Admin / Super Admin only"""
    users = User.objects.all().order_by('-date_joined')
    return render(request, 'app1/user_list.html', {'users': users})


@admin_required
def add_user(request):
    """Add a new user — Admin / Super Admin only"""
    form = AddUserForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        messages.success(request, f'User "{user.username}" created successfully.')
        return redirect('user_list')

    return render(request, 'app1/add_user.html', {'form': form})


@admin_required
def user_detail(request, user_id):
    """View a single user's details"""
    target_user = get_object_or_404(User, pk=user_id)
    return render(request, 'app1/user_detail.html', {'target_user': target_user})


@admin_required
def assign_units(request, user_id):
    """Assign unit_ids to a user — Admin / Super Admin only"""
    target_user = get_object_or_404(User, pk=user_id)

    initial = {
        'unit_ids_input': ', '.join(str(u) for u in target_user.get_unit_ids())
    }
    form = AssignUnitsForm(request.POST or None, initial=initial)

    if request.method == 'POST' and form.is_valid():
        target_user.unit_ids = form.cleaned_data['unit_ids_input']
        target_user.save()
        messages.success(request, f'Units updated for "{target_user.username}".')
        return redirect('user_list')

    return render(request, 'app1/assign_units.html', {
        'form': form,
        'target_user': target_user,
    })


@admin_required
def delete_user(request, user_id):
    """Delete a user — Admin / Super Admin only"""
    target_user = get_object_or_404(User, pk=user_id)

    # Prevent self-deletion
    if target_user == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect('user_list')

    if request.method == 'POST':
        username = target_user.username
        target_user.delete()
        messages.success(request, f'User "{username}" deleted.')
        return redirect('user_list')

    return render(request, 'app1/confirm_delete.html', {'target_user': target_user})

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
            reading_time = datetime.fromtimestamp(reading.timestamp, tz=EST)
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
        """
        Verification endpoint — returns raw registers alongside every decoded value
        with intermediate steps (_raw, _signed, final) so you can audit each register.
        URL: /api/readings/?unit_id=100&limit=1
        """
        unit_id = request.query_params.get('unit_id')
        limit   = int(request.query_params.get('limit', 10))

        queryset = SensorReading.objects.all()
        if unit_id:
            queryset = queryset.filter(unit_id=unit_id)
        queryset = queryset.order_by('-timestamp')[:limit]

        def to_signed_16(raw):
            """Two's complement unsigned→signed conversion for 16-bit Modbus registers."""
            if raw > 32767:
                return raw - 65536
            return raw

        results = []
        for reading in queryset:
            b  = reading.block1_raw or []
            b2 = reading.block2_raw or []

            def reg(idx, default=0):
                return b[idx] if len(b) > idx else default

            decoded = {
                # ── Voltage Phase-Neutral (scale ÷10) ─────────────────────
                "v_an_raw":     reg(0),  "v_an":     reg(0)  / 10,
                "v_bn_raw":     reg(1),  "v_bn":     reg(1)  / 10,
                "v_cn_raw":     reg(2),  "v_cn":     reg(2)  / 10,
                "v_avg_ln_raw": reg(3),  "v_avg_ln": reg(3)  / 10,

                # ── Voltage Line-Line (scale ÷10) ──────────────────────────
                "v_ab_raw":     reg(4),  "v_ab":     reg(4)  / 10,
                "v_bc_raw":     reg(5),  "v_bc":     reg(5)  / 10,
                "v_ca_raw":     reg(6),  "v_ca":     reg(6)  / 10,
                "v_avg_ll_raw": reg(7),  "v_avg_ll": reg(7)  / 10,

                # ── Current (scale ÷10) ────────────────────────────────────
                "i_a_raw":       reg(8),   "i_a":       reg(8)  / 10,
                "i_b_raw":       reg(9),   "i_b":       reg(9)  / 10,
                "i_c_raw":       reg(10),  "i_c":       reg(10) / 10,
                "i_avg_raw":     reg(11),  "i_avg":     reg(11) / 10,
                "i_demand_raw":  reg(12),  "i_demand":  reg(12) / 10,
                "i_peak_raw":    reg(13),  "i_peak":    reg(13) / 10,
                "i_neutral_raw": reg(14),  "i_neutral": reg(14) / 10,

                # ── Frequency (scale ÷100) ─────────────────────────────────
                "freq_raw": reg(15),
                "freq_hz":  reg(15) / 100,

                # ── Power Factor (signed, scale ÷10000) ───────────────────
                # FIXED: was ÷1000, correct scale is ÷10000
                # Proof: kW/kVA = 1.3/9.9 = 0.131
                #        signed(-1264) ÷ 10000 = 0.1264 ✅
                "pf_total_raw":    reg(16),
                "pf_total_signed": to_signed_16(reg(16)),
                "pf_total":        round(max(-1.0, min(1.0, to_signed_16(reg(16)) / 10000)), 4),

                "pf_a_raw":    reg(17),
                "pf_a_signed": to_signed_16(reg(17)),
                "pf_a":        round(max(-1.0, min(1.0, to_signed_16(reg(17)) / 10000)), 4),

                "pf_b_raw":    reg(18),
                "pf_b_signed": to_signed_16(reg(18)),
                "pf_b":        round(max(-1.0, min(1.0, to_signed_16(reg(18)) / 10000)), 4),

                "pf_c_raw":    reg(19),
                "pf_c_signed": to_signed_16(reg(19)),
                "pf_c":        round(max(-1.0, min(1.0, to_signed_16(reg(19)) / 10000)), 4),

                # ── Active Power (signed, scale ÷10) ──────────────────────
                "kw_raw":    reg(20),
                "kw_signed": to_signed_16(reg(20)),
                "kw":        round(to_signed_16(reg(20)) / 10, 2),

                # ── Reactive Power (signed, scale ÷10) ────────────────────
                "kvar_raw":    reg(21),
                "kvar_signed": to_signed_16(reg(21)),
                "kvar":        round(to_signed_16(reg(21)) / 10, 2),

                # ── Apparent Power (unsigned, scale ÷10) ──────────────────
                "kva_raw": reg(22),
                "kva":     round(reg(22) / 10, 2),

                # ── Energy 32-bit (block2, scale ÷100) ────────────────────
                "b2_low":            b2[0] if len(b2) > 0 else None,
                "b2_high":           b2[1] if len(b2) > 1 else None,
                "b2_combined_32bit": ((b2[1] << 16) | b2[0]) if len(b2) > 1 else None,
                "energy_kwh":        ((b2[1] << 16) | b2[0]) / 100 if len(b2) > 1 else None,

                # ── Sanity checks (computed vs meter register) ─────────────
                # These let you instantly spot any remaining discrepancies.
                "check_pf_from_power":   round(
                    abs(to_signed_16(reg(20)) / reg(22)) if reg(22) else 0, 4
                ),  # kW/kVA — should match pf_total magnitude
                "check_kva_from_triangle": round(
                    (
                        (to_signed_16(reg(20)) / 10) ** 2 +
                        (to_signed_16(reg(21)) / 10) ** 2
                    ) ** 0.5, 2
                ),  # √(kW²+kVAR²) — should match kva

                # ── DB cached values (for comparison with live decode) ─────
                "db_voltage":         reading.voltage,
                "db_current":         reading.current,
                "db_active_power_kw": reading.active_power_kw,
                "db_reactive_kvar":   reading.reactive_power_kvar,
                "db_apparent_kva":    reading.apparent_power_kva,
                "db_energy_kwh":      reading.energy_today_kwh,
            }

            results.append({
                "id":         reading.id,
                "unit_id":    reading.unit_id,
                "timestamp":  reading.timestamp,
                "block1_raw": b,
                "block2_raw": b2,
                "decoded":    decoded,
            })

        return Response(results)
    
    
# views.py

from datetime import datetime, time
import time as time_module
from django.utils.timezone import make_aware
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import *


class SensorReadingFilterView(APIView):
    """
    GET /api/readings/filter/
    Query params:
        - start_date (required): YYYY-MM-DD
        - end_date   (required): YYYY-MM-DD
        - unit_id    (optional): integer
    """

    DATE_FORMAT = "%Y-%m-%d"

    def get(self, request):
        start_date_str = request.query_params.get('start_date')
        end_date_str   = request.query_params.get('end_date')
        unit_id        = request.query_params.get('unit_id')

        # ── Validate presence ────────────────────────────────────────────
        missing = [f for f, v in [('start_date', start_date_str), ('end_date', end_date_str)] if not v]
        if missing:
            return Response(
                {"error": f"Missing required parameter(s): {', '.join(missing)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ── Validate & parse dates ───────────────────────────────────────
        try:
            start_dt = datetime.strptime(start_date_str, self.DATE_FORMAT)
            end_dt   = datetime.strptime(end_date_str,   self.DATE_FORMAT)
        except ValueError:
            return Response(
                {"error": "Invalid date format. Expected YYYY-MM-DD (e.g. 2026-05-01)."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if start_dt > end_dt:
            return Response(
                {"error": "start_date must not be later than end_date."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ── Convert to Unix timestamps (inclusive range) ─────────────────
        # start = 2026-05-01 00:00:00 → unix
        # end   = 2026-05-31 23:59:59 → unix
        start_ts = start_dt.replace(hour=0,  minute=0,  second=0,  microsecond=0).timestamp()
        end_ts   = end_dt.replace(  hour=23, minute=59, second=59, microsecond=999999).timestamp()

        # ── Build queryset ───────────────────────────────────────────────
        # Uses the composite index (unit_id, timestamp) when unit_id is
        # provided, and the single-column timestamp index otherwise.
        queryset = (
            SensorReading.objects
            .filter(timestamp__gte=start_ts, timestamp__lte=end_ts)
            .order_by('timestamp')               # ascending chronological
            .only(                               # skip heavy JSON columns
                'id', 'unit_id', 'timestamp', 'created_at',
                'voltage', 'current', 'active_power_kw',
                'reactive_power_kvar', 'apparent_power_kva', 'energy_today_kwh',
            )
        )

        if unit_id is not None:
            try:
                queryset = queryset.filter(unit_id=int(unit_id))
            except ValueError:
                return Response(
                    {"error": "unit_id must be a positive integer."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        serializer = SensorReadingFilterSerializer(queryset, many=True)
        return Response({
            "count":      queryset.count(),
            "start_date": start_date_str,
            "end_date":   end_date_str,
            "unit_id":    unit_id,
            "results":    serializer.data,
        })
        
        

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
                'timestamp': datetime.fromtimestamp(latest.timestamp, tz=EST).isoformat(),
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
            # === NEW: Always bucket into 1-minute intervals ===
            minute_data = {}
            for reading in readings:
                # Truncate to the minute
                dt = datetime.fromtimestamp(reading.timestamp, tz=EST)
                minute_key = dt.replace(second=0, microsecond=0)

                if minute_key not in minute_data:
                    minute_data[minute_key] = {'total': 0.0, 'count': 0}
                if reading.active_power_kw is not None:
                    minute_data[minute_key]['total'] += reading.active_power_kw
                    minute_data[minute_key]['count'] += 1

            labels = []
            values = []
            for minute_key in sorted(minute_data.keys()):
                entry = minute_data[minute_key]
                if entry['count'] == 0:
                    continue
                avg_val = entry['total'] / entry['count']

                # Label format depends on period
                if period == 'today':
                    labels.append(minute_key.strftime('%H:%M'))
                else:
                    labels.append(minute_key.strftime('%m-%d %H:%M'))

                values.append(round(avg_val, 3))

            data_type = 'minute_aggregated'
            count = len(labels)

            if labels:
                data = {
                    'labels': labels,
                    'values': values,
                    'unit': 'kW',
                    'type': data_type,
                    'count': count
                }
            else:
                data = {'labels': [], 'values': [], 'unit': 'kW', 'type': 'no_data', 'count': 0}
        else:
            data = {'labels': [], 'values': [], 'unit': 'kW', 'type': 'no_data', 'count': 0}

        return Response(data)

class RecentReadingsView(APIView):
    """
    Returns recent readings with ALL decoded parameters for the full dataset table.
    Decodes directly from raw registers on every request so the table always
    reflects the correct scaling logic without needing a DB migration.
    """

    def get(self, request):
        unit_id = request.query_params.get('unit_id', 100)
        limit = int(request.query_params.get('limit', 20))

        readings = SensorReading.objects.filter(unit_id=unit_id)[:limit]

        def to_signed_16(raw):
            """
            Unsigned 16-bit → signed 16-bit (two's complement).
            Required for: Power Factor, Active Power, Reactive Power.
            """
            if raw > 32767:
                return raw - 65536
            return raw

        data = []
        for reading in readings:
            dt = datetime.fromtimestamp(reading.timestamp, tz=EST)
            b = reading.block1_raw or []

            def reg(idx, default=0):
                """Safely read register by index, return default if missing."""
                return b[idx] if len(b) > idx else default

            # ── Voltage Phase-Neutral (÷10 → V) ──────────────────────
            v_an     = reg(0) / 10    # Phase A to Neutral (~279-281V in your system)
            v_bn     = reg(1) / 10    # Phase B to Neutral
            v_cn     = reg(2) / 10    # Phase C to Neutral
            v_avg_ln = reg(3) / 10    # Meter's own average L-N voltage

            # ── Voltage Line-Line (÷10 → V) ──────────────────────────
            v_ab     = reg(4) / 10    # Between Phase A and B (~484-486V)
            v_bc     = reg(5) / 10    # Between Phase B and C
            v_ca     = reg(6) / 10    # Between Phase C and A
            v_avg_ll = reg(7) / 10    # Meter's own average L-L voltage

            # ── Current (÷10 → A) ────────────────────────────────────
            i_a      = reg(8)  / 10   # Phase A current
            i_b      = reg(9)  / 10   # Phase B current
            i_c      = reg(10) / 10   # Phase C current
            i_avg    = reg(11) / 10   # Meter's average current
            i_demand = reg(12) / 10   # Current demand (rolling average)
            i_peak   = reg(13) / 10   # Historical peak demand current
            i_neutral= reg(14) / 10   # Neutral conductor current

            # ── Frequency (÷100 → Hz) ─────────────────────────────────
            # US grid standard = 60.00 Hz. Your readings: 59.97–60.00 Hz ✅
            freq = reg(15) / 100

            # ── Power Factor (signed ÷ 10000) ─────────────────────────
            # SCALE IS ÷10000, NOT ÷1000.
            # Verified by cross-check: kW/kVA = 1.3/9.9 = 0.131
            # signed(-1264) ÷ 10000 = -0.1264 ✅  (was clamping to -1.0 with ÷1000)
            #
            # Sign meaning:
            #   Positive PF = lagging  (inductive load — motors, transformers)
            #   Negative PF = leading  (capacitive load — PFC capacitors)
            # Your system shows ~0.13 PF which means mostly reactive (kVAR >> kW)
            pf_total = round(
                max(-1.0, min(1.0, to_signed_16(reg(16)) / 10000)), 4
            )
            pf_a = round(
                max(-1.0, min(1.0, to_signed_16(reg(17)) / 10000)), 4
            )
            pf_b = round(
                max(-1.0, min(1.0, to_signed_16(reg(18)) / 10000)), 4
            )
            pf_c = round(
                max(-1.0, min(1.0, to_signed_16(reg(19)) / 10000)), 4
            )

            # ── Power (signed ÷ 10) ───────────────────────────────────
            # Active Power: real energy consumed. Verified: raw=13 → 1.3 kW ✅
            # Reactive Power: can be negative for capacitive loads.
            # Apparent Power: always positive, unsigned.
            kw   = round(to_signed_16(reg(20)) / 10, 2)
            kvar = round(to_signed_16(reg(21)) / 10, 2)
            kva  = round(reg(22) / 10, 2)

            data.append({
                # ── Original columns (preserved, do not remove) ───────
                'time':    dt.strftime('%H:%M:%S'),
                'date':    dt.strftime('%Y-%m-%d'),
                'kw':      kw,
                # Use meter's own average L-N voltage (reg[3]), not manual average
                'voltage': round(v_avg_ln, 1),
                # Use meter's own average current (reg[11]), not manual average
                'current': round(i_avg, 1),
                'energy':  round(reading.energy_today_kwh, 2) if reading.energy_today_kwh else 0,
                'pf':      pf_total,
                'status':  'ok',

                # ── All voltage parameters ────────────────────────────
                'v_an':      round(v_an, 1),
                'v_bn':      round(v_bn, 1),
                'v_cn':      round(v_cn, 1),
                'v_avg_ln':  round(v_avg_ln, 1),
                'v_ab':      round(v_ab, 1),
                'v_bc':      round(v_bc, 1),
                'v_ca':      round(v_ca, 1),
                'v_avg_ll':  round(v_avg_ll, 1),

                # ── All current parameters ────────────────────────────
                'i_a':       round(i_a, 2),
                'i_b':       round(i_b, 2),
                'i_c':       round(i_c, 2),
                'i_avg':     round(i_avg, 2),
                'i_demand':  round(i_demand, 2),
                'i_peak':    round(i_peak, 2),
                'i_neutral': round(i_neutral, 2),

                # ── Frequency ─────────────────────────────────────────
                'frequency': round(freq, 2),

                # ── All power factor parameters (FIXED: ÷10000) ───────
                'pf_a':  pf_a,
                'pf_b':  pf_b,
                'pf_c':  pf_c,

                # ── All power parameters ──────────────────────────────
                'kvar': kvar,
                'kva':  kva,
            })

        return Response(data)
    
    

# ─────────────────────────────────────────────────────────────────────────────
# COMPANY VIEWS
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
def company_list(request):
    """List all companies — Device Management landing page"""
    companies = Company.objects.prefetch_related('devices').all()
    return render(request, 'app1/company_list.html', {'companies': companies})


@admin_required
def add_company(request):
    """Create a new company"""
    form = CompanyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        company = form.save(commit=False)
        company.created_by = request.user
        company.save()
        messages.success(request, f'Company "{company.name}" created successfully.')
        return redirect('company_detail', company_id=company.pk)
    return render(request, 'app1/add_company.html', {'form': form})


@admin_required
def edit_company(request, company_id):
    """Edit an existing company"""
    company = get_object_or_404(Company, pk=company_id)
    form = CompanyForm(request.POST or None, instance=company)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Company "{company.name}" updated.')
        return redirect('company_detail', company_id=company.pk)
    return render(request, 'app1/edit_company.html', {'form': form, 'company': company})


@admin_required
def delete_company(request, company_id):
    """Delete a company"""
    company = get_object_or_404(Company, pk=company_id)
    if request.method == 'POST':
        name = company.name
        company.delete()
        messages.success(request, f'Company "{name}" deleted.')
        return redirect('company_list')
    return render(request, 'app1/confirm_delete_company.html', {'company': company})


@admin_required
def company_detail(request, company_id):
    """Company detail page — shows all assigned devices and their latest readings"""
    company = get_object_or_404(Company, pk=company_id)
    devices = company.devices.prefetch_related('assigned_users').all()

    # Attach latest reading to each device
    device_data = []
    for device in devices:
        try:
            latest = SensorReading.objects.filter(unit_id=device.unit_id).latest('timestamp')
            device_data.append({'device': device, 'latest': latest})
        except SensorReading.DoesNotExist:
            device_data.append({'device': device, 'latest': None})

    return render(request, 'app1/company_detail.html', {
        'company': company,
        'device_data': device_data,
    })


# ─────────────────────────────────────────────────────────────────────────────
# DEVICE VIEWS
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
def device_list(request):
    """List all devices across all companies"""
    devices = Device.objects.select_related('company').prefetch_related('assigned_users').all()
    return render(request, 'app1/device_list.html', {'devices': devices})


@admin_required
def add_device(request):
    """Add a new device globally"""
    form = DeviceForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        device = form.save()
        messages.success(request, f'Device "{device}" created.')
        return redirect(energy_dashboard_v2)
    return render(request, 'app1/add_device.html', {'form': form})


@admin_required
def edit_device(request, device_id):
    """Edit device settings"""
    device = get_object_or_404(Device, pk=device_id)
    form = DeviceForm(request.POST or None, instance=device)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Device "{device}" updated.')
        return redirect('device_detail', device_id=device.pk)
    return render(request, 'app1/edit_device.html', {'form': form, 'device': device})


@admin_required
def delete_device(request, device_id):
    """Delete a device"""
    device = get_object_or_404(Device, pk=device_id)
    company_id = device.company_id
    if request.method == 'POST':
        name = str(device)
        device.delete()
        messages.success(request, f'Device "{name}" deleted.')
        if company_id:
            return redirect('company_detail', company_id=company_id)
        return redirect('device_list')
    return render(request, 'app1/confirm_delete_device.html', {'device': device})


@login_required(login_url='login')
def device_detail(request, device_id):
    """Device detail / mini dashboard — accessible by assigned users too"""
    device = get_object_or_404(Device, pk=device_id)

    # Access control
    if not request.user.is_admin_or_superadmin():
        if not device.assigned_users.filter(pk=request.user.pk).exists():
            return HttpResponseForbidden("<h2>403</h2><p>You don't have access to this device.</p>")

    try:
        latest = SensorReading.objects.filter(unit_id=device.unit_id).latest('timestamp')
    except SensorReading.DoesNotExist:
        latest = None

    return render(request, 'app1/device_detail.html', {
        'device': device,
        'latest': latest,
    })
    
    
class DevicesByCategoryView(APIView):
    """
    Returns all active devices grouped by device_category.
    Used by energy_dashboard_v2 navbar to dynamically populate dropdowns.
 
    GET /api/devices/by-category/
    Optional query param: ?unit_ids=100,101,102  (comma-separated, for non-admin users)
 
    Response shape:
    {
      "Meters": [
        {"unit_id": 100, "display_name": "Main Panel", "location": "Bldg A"},
        ...
      ],
      "Solar Generation": [...],
      ...
    }
    """
    permission_classes = []   # adjust to your auth scheme if needed
 
    def get(self, request):
        # Respect user access control
        user = request.user
        queryset = Device.objects.filter(is_active=True).select_related('company')
 
        if not user.is_authenticated:
            return Response({})
 
        if not user.is_admin_or_superadmin():
            allowed = user.get_unit_ids()
            if allowed:
                queryset = queryset.filter(unit_id__in=allowed)
            else:
                return Response({})
 
        # Group by category
        ALL_CATEGORIES = [
            'Meters', 'Solar Generation', 'Battery Power',
            'EV Charging', 'Building Load', 'HVAC', 'Genset',
        ]

        # Pre-populate all categories with empty lists
        result = {cat: [] for cat in ALL_CATEGORIES}

        for device in queryset.order_by('device_category', 'unit_id'):
            cat = device.device_category
            if cat not in result:
                result[cat] = []   # safety net for any unknown category
            result[cat].append({
                'unit_id': device.unit_id,
                'display_name': device.get_display_name(),
                'location': device.location or '',
                'company': device.company.name if device.company else '',
                'per_unit_cost': str(device.per_unit_cost) if device.per_unit_cost else None,
            })

        return Response(result)
    
@login_required(login_url='login')
def energy_dashboard_v2(request):
    """
    Energy Dashboard v2 — dynamic multi-device dashboard.
    Passes the primary unit and accessible unit list to the template.
    The navbar device list is loaded client-side via /api/devices/by-category/.
    """
    if request.user.is_admin_or_superadmin():
        # Admin sees all devices
        from .models import Device as DeviceModel
        unit_ids = list(DeviceModel.objects.filter(is_active=True).values_list('unit_id', flat=True))
        if not unit_ids:
            unit_ids = [100]
    else:
        unit_ids = request.user.get_unit_ids() or [100]
 
    primary_unit = unit_ids[0] if unit_ids else 100
 
    context = {
        'user': request.user,
        'unit_ids': unit_ids,
        'primary_unit': primary_unit,
    }
    return render(request, 'app1/energy_dashboard_v2.html', context)


def home(request):
    return render(request, 'app1/home.html')