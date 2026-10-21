from django.shortcuts import render

# Create your views here.

from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Avg, Max, Min, Sum
from django.utils import timezone
from datetime import timezone as dt_timezone
from datetime import datetime, timedelta
from datetime import datetime, timedelta, timezone as dt_timezone
import logging
from .models import *
from .forms import *
from django.db import transaction
from reportlab.platypus import Image
from django.conf import settings
from django.contrib.staticfiles import finders
import os
from PIL import Image as PILImage

from .models import SensorReading, HourlyAggregate
from .serializers import SensorReadingSerializer, LatestReadingSerializer, SummaryStatsSerializer, GraphDataSerializer
from .escalator import get_escalated_cost_breakdown, calculate_revenue
from django.http import HttpResponse, JsonResponse, FileResponse

logger = logging.getLogger(__name__)

def _display_name(user):
    """First name, capitalized, falling back to username. Used in
    greeting messages so 'john' displays as 'John'."""
    name = (user.first_name or user.username or '').strip()
    return name[:1].upper() + name[1:] if name else name

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
SELECTED_OPTION_KEY = 'selected_landing_option'

FUNCTION_OPTIONS = {
    'design': {
        'label': 'Design a Microgrid',
        'description': 'Upload Interval Data and Electric Bill/s to Model Solar + battery-based microgrids and Create Techno/Financial Proposals',
        'icon': '🛠️',
    },
    'operate': {
        'label': 'Operate Microgrid (VPP)',
        'description': 'Operate Your Solar, Microgrids, and VPP portfolio',
        'icon': '⚡',
    },
    'commission': {
        'label': 'Set Up, Configure & Commission a System',
        'description': '',  # Vijay: no further explanation needed
        'icon': '📋',
    },
}


def landing_view(request):
    """Public landing page — shown to everyone, authenticated or not.
    Operate users get routed straight to company_list from
    _post_auth_redirect right after OTP/signup — that redirect does NOT
    go through this view, so this page staying visible to logged-in
    users doesn't affect that flow. It's what lets Design/Commission
    users land back here (not the dashboard) after Under Construction."""
    return render(request, 'app1/landing.html', {'options': FUNCTION_OPTIONS})

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

@csrf_exempt
@require_POST
def beacon_logout(request):
    """Called via navigator.sendBeacon when the tab/browser is closing.
    CSRF-exempt because sendBeacon can't attach a token, and POST-only
    to avoid this being triggerable via a simple GET/link. Only ever
    does one thing: kill the current session. No response body needed —
    the browser is already unloading the page by the time this runs."""
    if request.user.is_authenticated:
        logout(request)
    return HttpResponse(status=204)

def select_option(request, option):
    if option not in FUNCTION_OPTIONS:
        messages.error(request, 'Please choose a valid option.')
        return redirect('landing')

    request.session[SELECTED_OPTION_KEY] = option

    if request.user.is_authenticated:
        if option in ('design', 'commission'):
            return redirect('coming_soon', option=option)
        return redirect('company_list')

    return redirect('check_email')


def _post_auth_redirect(request, is_new_signup=False):
    """After successful login/signup, honor whichever tile they picked
    on the landing page.

    - New signup, Design/Commission -> the one-time Welcome page
      (with the Continue button), per the spec.
    - Existing user (OTP verified), Design/Commission -> straight to
      the Under Construction page, no Welcome page.
    - Operate, either case -> company_list, whose existing role/access
      logic (including the "no microgrids yet" welcome card for a
      brand-new user) is completely unchanged.
    """
    option = request.session.pop(SELECTED_OPTION_KEY, None)

    if option and request.user.is_authenticated:
        if request.user.signup_intent != option:
            request.user.signup_intent = option
            request.user.save(update_fields=['signup_intent'])

    if option == 'design':
        return redirect('design_welcome') if is_new_signup else redirect('coming_soon', option='design')
    if option == 'commission':
        return redirect('commission_welcome') if is_new_signup else redirect('coming_soon', option='commission')
    return redirect('company_list')  # operate, or no option selected

@login_required(login_url='login')
def design_welcome(request):
    """One-time welcome shown right after account creation for the
    'Design a Microgrid' path — NOT shown to existing users who
    OTP-verify (they go straight to coming_soon instead)."""
    return render(request, 'app1/design_welcome.html', {
        'label': FUNCTION_OPTIONS['design']['label'],
    })


@login_required(login_url='login')
def commission_welcome(request):
    """Same as design_welcome, for the Commission path."""
    return render(request, 'app1/commission_welcome.html', {
        'label': FUNCTION_OPTIONS['commission']['label'],
    })

from django.core.mail import send_mail
from .models import OTPCode

OTP_TTL_MINUTES = 10
KNOWN_EMAIL_COOKIE = 'rock_known_email'
KNOWN_EMAIL_COOKIE_MAX_AGE = 60 * 60 * 24 * 90  # 90 days — "recognized device"


def _send_otp(email):
    """
    Create + send an OTP for this email. Email-based for now — SMS/phone
    is a paid-gateway v2 item (per the Ansar/Hussein scoping discussion).
    """
    otp = OTPCode.generate_for(email, purpose='login', ttl_minutes=OTP_TTL_MINUTES)
    try:
        send_mail(
            subject='Your 3rd Rock Energy verification code',
            message=(
                f'Your verification code is: {otp.code}\n\n'
                f'This code expires in {OTP_TTL_MINUTES} minutes.'
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@3rdrockenergy.com'),
            recipient_list=[email],
            fail_silently=True,
        )
    except Exception as e:
        logger.error(f"Failed to send OTP email to {email}: {e}")
    return otp


def check_email(request):
    if request.user.is_authenticated:
        return redirect('company_list')

    known_email = request.COOKIES.get(KNOWN_EMAIL_COOKIE)
    if known_email and User.objects.filter(email__iexact=known_email).exists():
        request.session['recognized_email'] = known_email
        _send_otp(known_email)
        messages.info(request, f"We've sent a verification code to {known_email}.")
        return redirect('verify_otp')

    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip().lower()
        if not email:
            messages.error(request, 'Please enter your email address.')
            return render(request, 'app1/check_email.html')

        if User.objects.filter(email__iexact=email).exists():
            request.session['recognized_email'] = email
            _send_otp(email)
            messages.info(request, f"We've sent a verification code to {email}.")
            return redirect('verify_otp')

        request.session['recognized_email'] = email
        return redirect('signup')

    return render(request, 'app1/check_email.html')

def verify_otp(request):
    """Enter the 6-digit code sent to the recognized email."""
    if request.user.is_authenticated:
        return redirect('company_list')

    email = request.session.get('recognized_email')
    if not email:
        messages.error(request, 'Please enter your email address first.')
        return redirect('check_email')

    if request.method == 'POST':
        code = (request.POST.get('code') or '').strip()
        otp = OTPCode.objects.filter(
            email__iexact=email, purpose='login', is_used=False
        ).order_by('-created_at').first()

        if not otp or not otp.is_valid():
            messages.error(request, 'That code has expired. Please request a new one.')
            return render(request, 'app1/verify_otp.html', {'email': email})

        otp.attempts += 1
        otp.save(update_fields=['attempts'])

        if code != otp.code:
            messages.error(request, 'Incorrect code. Please try again.')
            return render(request, 'app1/verify_otp.html', {'email': email})

        otp.is_used = True
        otp.save(update_fields=['is_used'])

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            messages.error(request, 'Account not found.')
            return redirect('check_email')

        login(request, user, backend='app1.backends.EmailBackend')
        request.session.pop('recognized_email', None)
        messages.success(request, f'Welcome back, {_display_name(user)}!')

        response = (_post_auth_redirect(request, is_new_signup=False)
                    if SELECTED_OPTION_KEY in request.session
                    else redirect('company_list'))
        response.set_cookie(
            KNOWN_EMAIL_COOKIE, email,
            max_age=KNOWN_EMAIL_COOKIE_MAX_AGE, httponly=True, samesite='Lax'
        )
        return response

    return render(request, 'app1/verify_otp.html', {'email': email})

def resend_otp(request):
    email = request.session.get('recognized_email')
    if not email:
        return redirect('check_email')
    _send_otp(email)
    messages.info(request, f"A new code has been sent to {email}.")
    return redirect('verify_otp')


def use_different_email(request):
    """'Not you?' link — clears the recognized email/cookie and starts over.
    This is deliberately NOT an auto-fill/guess — the user has to type
    their own email again, per Vijay's explicit instruction."""
    request.session.pop('recognized_email', None)
    response = redirect('check_email')
    response.delete_cookie(KNOWN_EMAIL_COOKIE)
    return response


@login_required(login_url='login')
def coming_soon_view(request, option):
    """Under Construction page for Design/Commission. Reached either
    directly after OTP verification for an EXISTING user (no Welcome
    page shown), or via the Welcome page's Continue button for a
    brand-new signup."""
    if option not in ('design', 'commission'):
        return redirect('landing')
    return render(request, 'app1/coming_soon.html', {
        'label': FUNCTION_OPTIONS[option]['label'],
    })

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
        return redirect('company_list')

    initial = {}
    recognized_email = request.session.get('recognized_email')
    if recognized_email:
        initial['email'] = recognized_email

    form = LoginForm(request, data=request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)
        request.session.pop('recognized_email', None)
        messages.success(request, f'Welcome back, {_display_name(user)}!')
        if SELECTED_OPTION_KEY in request.session:
            return _post_auth_redirect(request, is_new_signup=False)
        next_url = request.GET.get('next', 'company_list')
        return redirect(next_url)

    return render(request, 'app1/login.html', {'form': form})

def signup_view(request):
    """Sign Up page – creates a normal user. Email-only, no password —
    OTP is the only login mechanism now."""
    if request.user.is_authenticated:
        return redirect('company_list')

    initial = {}
    recognized_email = request.session.get('recognized_email')
    if recognized_email:
        initial['email'] = recognized_email

    form = SignUpForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        user.backend = 'app1.backends.EmailBackend'
        login(request, user, backend='app1.backends.EmailBackend')
        request.session.pop('recognized_email', None)
        messages.success(request, f'Account created! Welcome, {_display_name(user)}!')
        if SELECTED_OPTION_KEY in request.session:
            return _post_auth_redirect(request, is_new_signup=True)
        return redirect('company_list')

    return render(request, 'app1/signup.html', {'form': form})


def keepalive(request):
    """Only refreshes the session timer (SESSION_SAVE_EVERY_REQUEST is True)."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    return HttpResponse(status=204)

def logout_view(request):
    logout(request)
    messages.info(request, 'You have been signed out.')
    response = redirect('landing')
    response.delete_cookie(KNOWN_EMAIL_COOKIE)
    return response

from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied


def superadmin_required(view_func):
    """Restrict view to superadmin only (e.g. creating/deleting companies)."""
    @wraps(view_func)
    @login_required(login_url='login')
    def wrapper(request, *args, **kwargs):
        if request.user.role != 'superadmin':
            return HttpResponseForbidden(
                "<h2>403 – Forbidden</h2><p>Super Admin access required.</p>"
            )
        return view_func(request, *args, **kwargs)
    return wrapper


def check_unit_access(request, unit_id):
    """Raise DRF PermissionDenied (403) if the user can't access this unit_id."""
    if not request.user.can_access_unit(unit_id):
        raise PermissionDenied(f"You do not have access to unit_id={unit_id}.")
    
def _shares_company(request, target_user):
    if request.user.role == 'superadmin':
        return True
    return target_user.companies.filter(pk__in=request.user.companies.all()).exists()

def check_company_access(request, company_id):
    if not request.user.can_access_company(company_id):
        raise PermissionDenied("You do not have access to this microgrid.")
    
def write_required(view_func):
    """Blocks User/Operator from state-changing page views. Admin/Super Admin pass through."""
    @wraps(view_func)
    @login_required(login_url='login')
    def wrapper(request, *args, **kwargs):
        if not request.user.can_write():
            return HttpResponseForbidden(
                "<h2>403 – Forbidden</h2><p>You have view-only access.</p>"
            )
        return view_func(request, *args, **kwargs)
    return wrapper

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
    if request.user.role == 'superadmin':
        users = User.objects.all().order_by('-date_joined')
    else:
        # Regular Users, Operators, and Admins should never see
        # Super Admins in their Personnel list — that's a level
        # above what they're allowed to view.
        users = (User.objects
                 .filter(companies__in=request.user.companies.all())
                 .exclude(role='superadmin')
                 .distinct()
                 .order_by('-date_joined'))

    role_counts = {
        'superadmin': users.filter(role='superadmin').count(),
        'admin': users.filter(role='admin').count(),
        'operator': users.filter(role='operator').count(),
        'user': users.filter(role='user').count(),
    }

    return render(request, 'app1/user_list.html', {
        'users': users,
        'total_count': users.count(),
        'role_counts': role_counts,
        'show_superadmin_stat': request.user.role == 'superadmin',
    })

@write_required
def add_user(request):
    # Coming from a customer page (?customer=ID): pre-fill the customer's contact
    # email / phone and tick that customer's microgrids, so nothing is typed twice.
    initial = {}
    customer_id = request.GET.get('customer', '')
    if customer_id.isdigit():
        customer = Customer.objects.filter(pk=int(customer_id)).first()
        if customer and request.user.can_access_customer(customer.pk):
            initial = {
                'email': customer.contact_email,
                'phone': customer.contact_phone,
                'companies': list(customer.microgrids.values_list('pk', flat=True)),
            }
    form = AddUserForm(request.POST or None, requesting_user=request.user, initial=initial)
    if request.method == 'POST' and form.is_valid():
        user = form.save()  # form's own save() sets username, unusable password, and companies m2m
        messages.success(
            request,
            f'User "{user.email}" created. Next step: this person can already see the microgrids '
            f'you ticked. To give access to a single DER, open that DER and use Edit, then "Assign to Persons".'
        )
        return redirect('user_list')
    return render(request, 'app1/add_user.html', {'form': form})

@write_required
def edit_user(request, user_id):
    target_user = get_object_or_404(User, pk=user_id)
    if request.user.role != 'superadmin':
        shared = target_user.companies.filter(pk__in=request.user.companies.all()).exists()
        if not shared and target_user.pk != request.user.pk:
            return HttpResponseForbidden("<h2>403</h2><p>You don't have access to this user.</p>")

    form = EditUserForm(request.POST or None, instance=target_user, requesting_user=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'User "{target_user.username}" updated.')
        return redirect('user_list')
    return render(request, 'app1/edit_user.html', {'form': form, 'target_user': target_user})

@admin_required
def user_detail(request, user_id):
    target_user = get_object_or_404(User, pk=user_id)
    if not _shares_company(request, target_user):
        return HttpResponseForbidden("<h2>403</h2><p>You don't have access to this Person.</p>")
    return render(request, 'app1/user_detail.html', {'target_user': target_user})

@write_required
def assign_units(request, user_id):
    """Assign unit_ids to a user — Admin / Super Admin only"""
    target_user = get_object_or_404(User, pk=user_id)
    if not _shares_company(request, target_user):
        return HttpResponseForbidden("<h2>403</h2><p>You don't have access to this Person.</p>")

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


@write_required
def delete_user(request, user_id):
    target_user = get_object_or_404(User, pk=user_id)
    if not _shares_company(request, target_user):
        return HttpResponseForbidden("<h2>403</h2><p>You don't have access to this Person.</p>")
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
                "kw":        round(to_signed_16(reg(20)) / 100, 2),

                # ── Reactive Power (signed, scale ÷10) ────────────────────
                "kvar_raw":    reg(21),
                "kvar_signed": to_signed_16(reg(21)),
                "kvar":        round(to_signed_16(reg(21)) / 10, 2),

                # ── Apparent Power (unsigned, scale ÷10) ──────────────────
                "kva_raw": reg(22),
                "kva":     round(reg(22) / 100, 2),

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
                        (to_signed_16(reg(20)) / 100) ** 2 +
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
    
import json
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import JsonResponse

@csrf_exempt
@require_POST
def post_inverter_reading(request):
    """
    POST endpoint for inverter telemetry. Stores the full detailed payload
    in InverterReading, AND writes a simplified row into SensorReading
    (active_power_kw + energy_today_kwh) so the existing dashboard, charts,
    revenue and billing logic — built around SensorReading — works for
    this device automatically, without any changes.
    """
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    unit_id = payload.get('unit_id')
    timestamp = payload.get('timestamp')
    data = payload.get('data') or {}

    if not unit_id or timestamp is None:
        return JsonResponse({'error': 'unit_id and timestamp are required'}, status=400)

    phase_currents = data.get('ac_phase_currents_a') or {}
    phase_voltages = data.get('ac_phase_to_neutral_voltages_v') or {}
    phase_to_phase = data.get('ac_phase_to_phase_voltages_v') or {}

    # 1. Full detailed record
    InverterReading.objects.create(
        unit_id=unit_id,
        device_type=payload.get('device_type', 'inverter'),
        timestamp=timestamp,
        modbus_address=data.get('modbus_address'),
        operating_state=data.get('operating_state'),
        ac_total_current_a=data.get('ac_total_current_a'),
        ac_phase_a_current_a=phase_currents.get('phase_a'),
        ac_phase_b_current_a=phase_currents.get('phase_b'),
        ac_phase_c_current_a=phase_currents.get('phase_c'),
        ac_phase_a_voltage_v=phase_voltages.get('phase_a'),
        ac_phase_b_voltage_v=phase_voltages.get('phase_b'),
        ac_phase_c_voltage_v=phase_voltages.get('phase_c'),
        ac_phase_ab_voltage_v=phase_to_phase.get('phase_ab'),
        ac_phase_bc_voltage_v=phase_to_phase.get('phase_bc'),
        ac_phase_ca_voltage_v=phase_to_phase.get('phase_ca'),
        ac_active_power_w=data.get('ac_active_power_w'),
        ac_apparent_power_va=data.get('ac_apparent_power_va'),
        ac_reactive_power_var=data.get('ac_reactive_power_var'),
        power_factor=data.get('power_factor'),
        grid_frequency_hz=data.get('grid_frequency_hz'),
        lifetime_energy_kwh=data.get('lifetime_energy_kwh'),
        dc_total_current_a=data.get('dc_total_current_a'),
        dc_voltage_v=data.get('dc_voltage_v'),
        dc_power_w=data.get('dc_power_w'),
        cabinet_temperature_c=data.get('cabinet_temperature_c'),
        mppt_total_power_w=data.get('mppt_total_power_w'),
        mppt_inputs=data.get('mppt_inputs', []),
    )

    # 2. Simplified bridge row — makes this device work with the existing
    # dashboard/revenue/billing system with zero changes to that code.
    device = Device.objects.filter(unit_id=unit_id).first()
    scale = device.get_scale_factor_at(timestamp) if device else 1.0

    ac_active_power_w = data.get('ac_active_power_w')
    lifetime_kwh = data.get('lifetime_energy_kwh')

    # --- small helpers (empty values stay empty) ---
    def _avg(*vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _div(value, divisor):
        return round(value / divisor, 3) if value is not None else None

    # --- pick the values out of the payload ---
    v_an, v_bn, v_cn = (phase_voltages.get(k) for k in ('phase_a', 'phase_b', 'phase_c'))
    v_ab, v_bc, v_ca = (phase_to_phase.get(k) for k in ('phase_ab', 'phase_bc', 'phase_ca'))
    i_a, i_b, i_c = (phase_currents.get(k) for k in ('phase_a', 'phase_b', 'phase_c'))

    avg_current = _avg(i_a, i_b, i_c)
    if avg_current is None:
        avg_current = data.get('ac_total_current_a')

    SensorReading.objects.create(
        unit_id=unit_id,
        timestamp=timestamp,

        # NEW: extra values so the existing cards and the "Complete Readings"
        # table can show them. They stay empty when the inverter sends none.
        voltage=_avg(v_an, v_bn, v_cn),
        current=avg_current,
        apparent_power_kva=_div(data.get('ac_apparent_power_va'), 1000),
        reactive_power_kvar=_div(data.get('ac_reactive_power_var'), 1000),
        voltage_a_n=v_an, voltage_b_n=v_bn, voltage_c_n=v_cn,
        voltage_a_b=v_ab, voltage_b_c=v_bc, voltage_c_a=v_ca,
        voltage_avg_ll=_avg(v_ab, v_bc, v_ca),
        current_a=i_a, current_b=i_b, current_c=i_c,
        frequency_hz=data.get('grid_frequency_hz'),
        pf_total=data.get('power_factor'),

        # EXISTING lines (unchanged):
        # inverter sends Watts — convert to kW to match SensorReading's convention.
        active_power_kw=round((ac_active_power_w / 1000) * scale, 2) if ac_active_power_w is not None else None,
        # inverter's lifetime_energy_kwh is ALREADY a clean, correctly-decoded
        # cumulative total, so we trust it directly, scaled like other devices.
        energy_today_kwh=round(lifetime_kwh * scale, 3) if lifetime_kwh is not None else None,
    )

    return JsonResponse({'status': 'ok'}, status=201)

def get_inverter_readings(request):
    """
    GET /api/inverter-readings/                  -> readings for ALL unit_ids, newest first
    GET /api/inverter-readings/?unit_id=E108     -> readings for that unit only
    Optional query params:
      - unit_id: filter to one unit (optional)
      - start: epoch timestamp, only readings at/after this time
      - end: epoch timestamp, only readings at/before this time
      - limit: max rows to return (default 500, hard cap 2000)
    """
    unit_id = request.GET.get('unit_id')

    readings = InverterReading.objects.all()
    if unit_id:
        readings = readings.filter(unit_id=unit_id)

    start = request.GET.get('start')
    end = request.GET.get('end')
    try:
        if start:
            readings = readings.filter(timestamp__gte=float(start))
        if end:
            readings = readings.filter(timestamp__lte=float(end))
    except ValueError:
        return JsonResponse({'error': 'start and end must be epoch timestamps'}, status=400)

    readings = readings.order_by('-timestamp', '-id')

    try:
        limit = min(int(request.GET.get('limit', 500)), 2000)
    except (TypeError, ValueError):
        limit = 500
    readings = list(readings[:limit])

    if not readings:
        msg = 'No data found for this unit_id' if unit_id else 'No inverter data found'
        return JsonResponse({'error': msg}, status=404)

    results = []
    for reading in readings:
        results.append({
            'unit_id': reading.unit_id,
            'device_type': reading.device_type,
            'timestamp': reading.timestamp,
            'data': {
                'modbus_address': reading.modbus_address,
                'operating_state': reading.operating_state,
                'ac_total_current_a': reading.ac_total_current_a,
                'ac_phase_currents_a': {
                    'phase_a': reading.ac_phase_a_current_a,
                    'phase_b': reading.ac_phase_b_current_a,
                    'phase_c': reading.ac_phase_c_current_a,
                },
                'ac_phase_to_neutral_voltages_v': {
                    'phase_a': reading.ac_phase_a_voltage_v,
                    'phase_b': reading.ac_phase_b_voltage_v,
                    'phase_c': reading.ac_phase_c_voltage_v,
                },
                'ac_phase_to_phase_voltages_v': {
                    'phase_ab': reading.ac_phase_ab_voltage_v,
                    'phase_bc': reading.ac_phase_bc_voltage_v,
                    'phase_ca': reading.ac_phase_ca_voltage_v,
                },
                'ac_active_power_w': reading.ac_active_power_w,
                'ac_apparent_power_va': reading.ac_apparent_power_va,
                'ac_reactive_power_var': reading.ac_reactive_power_var,
                'power_factor': reading.power_factor,
                'grid_frequency_hz': reading.grid_frequency_hz,
                'lifetime_energy_kwh': reading.lifetime_energy_kwh,
                'dc_total_current_a': reading.dc_total_current_a,
                'dc_voltage_v': reading.dc_voltage_v,
                'dc_power_w': reading.dc_power_w,
                'cabinet_temperature_c': reading.cabinet_temperature_c,
                'mppt_total_power_w': reading.mppt_total_power_w,
                'mppt_inputs': reading.mppt_inputs,
            },
        })

    return JsonResponse({'results': results})
    
    
# views.py

from datetime import datetime, time
import time as time_module
from django.utils.timezone import make_aware
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import *


def to_signed_16(raw):
    """Two's complement unsigned→signed conversion for 16-bit Modbus registers."""
    if raw > 32767:
        return raw - 65536
    return raw


def decode_reading(reading):
    """
    Build the full decoded dict (raw + intermediate + final) for one
    SensorReading. Mirrors the verification decode in SensorReadingAPIView.
    """
    b  = reading.block1_raw or []
    b2 = reading.block2_raw or []

    def reg(idx, default=0):
        return b[idx] if len(b) > idx else default

    decoded = {
        # ── Voltage Phase-Neutral (÷10) ──────────────────────────────
        "v_an_raw":     reg(0),  "v_an":     reg(0) / 10,
        "v_bn_raw":     reg(1),  "v_bn":     reg(1) / 10,
        "v_cn_raw":     reg(2),  "v_cn":     reg(2) / 10,
        "v_avg_ln_raw": reg(3),  "v_avg_ln": reg(3) / 10,

        # ── Voltage Line-Line (÷10) ──────────────────────────────────
        "v_ab_raw":     reg(4),  "v_ab":     reg(4) / 10,
        "v_bc_raw":     reg(5),  "v_bc":     reg(5) / 10,
        "v_ca_raw":     reg(6),  "v_ca":     reg(6) / 10,
        "v_avg_ll_raw": reg(7),  "v_avg_ll": reg(7) / 10,

        # ── Current (÷10) ────────────────────────────────────────────
        "i_a_raw":       reg(8),   "i_a":       reg(8)  / 10,
        "i_b_raw":       reg(9),   "i_b":       reg(9)  / 10,
        "i_c_raw":       reg(10),  "i_c":       reg(10) / 10,
        "i_avg_raw":     reg(11),  "i_avg":     reg(11) / 10,
        "i_demand_raw":  reg(12),  "i_demand":  reg(12) / 10,
        "i_peak_raw":    reg(13),  "i_peak":    reg(13) / 10,
        "i_neutral_raw": reg(14),  "i_neutral": reg(14) / 10,

        # ── Frequency (÷100) ─────────────────────────────────────────
        "freq_raw": reg(15),
        "freq_hz":  reg(15) / 100,

        # ── Power Factor (signed, ÷10000) ────────────────────────────
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

        # ── Active Power (signed, ÷10) ───────────────────────────────
        "kw_raw":    reg(20),
        "kw_signed": to_signed_16(reg(20)),
        "kw":        round(to_signed_16(reg(20)) / 100, 2),

        # ── Reactive Power (signed, ÷10) ─────────────────────────────
        "kvar_raw":    reg(21),
        "kvar_signed": to_signed_16(reg(21)),
        "kvar":        round(to_signed_16(reg(21)) / 10, 2),

        # ── Apparent Power (unsigned, ÷10) ───────────────────────────
        "kva_raw": reg(22),
        "kva":     round(reg(22) / 100, 2),

        # ── Energy 32-bit (block2, ÷100) ─────────────────────────────
        "b2_low":            b2[0] if len(b2) > 0 else None,
        "b2_high":           b2[1] if len(b2) > 1 else None,
        "b2_combined_32bit": ((b2[1] << 16) | b2[0]) if len(b2) > 1 else None,
        "energy_kwh":        ((b2[1] << 16) | b2[0]) / 100 if len(b2) > 1 else None,

        # ── Sanity checks ────────────────────────────────────────────
        "check_pf_from_power": round(
            abs(to_signed_16(reg(20)) / reg(22)) if reg(22) else 0, 4
        ),
        "check_kva_from_triangle": round(
            (
                (to_signed_16(reg(20)) / 100) ** 2 +
                (to_signed_16(reg(21)) / 10) ** 2
            ) ** 0.5, 2
        ),

        # ── DB cached values ─────────────────────────────────────────
        "db_voltage":         reading.voltage,
        "db_current":         reading.current,
        "db_active_power_kw": reading.active_power_kw,
        "db_reactive_kvar":   reading.reactive_power_kvar,
        "db_apparent_kva":    reading.apparent_power_kva,
        "db_energy_kwh":      reading.energy_today_kwh,
    }

    return {
        "id":         reading.id,
        "unit_id":    reading.unit_id,
        "timestamp":  reading.timestamp,
        "block1_raw": b,
        "block2_raw": b2,
        "decoded":    decoded,
    }

class SensorReadingFilterView(APIView):
    """
    GET /api/readings/filter/
    Query params:
        - start_date (required): YYYY-MM-DD
        - end_date   (required): YYYY-MM-DD
        - unit_id    (optional): integer

    Returns full raw + decoded data for every reading in the range.
    """

    DATE_FORMAT = "%Y-%m-%d"

    def get(self, request):
        start_date_str = request.query_params.get('start_date')
        end_date_str   = request.query_params.get('end_date')
        unit_id        = request.query_params.get('unit_id')

        # ── Validate presence ────────────────────────────────────────
        missing = [f for f, v in [('start_date', start_date_str),
                                   ('end_date', end_date_str)] if not v]
        if missing:
            return Response(
                {"error": f"Missing required parameter(s): {', '.join(missing)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ── Validate & parse dates ───────────────────────────────────
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

        # ── Convert to Unix timestamps (inclusive range) ─────────────
        start_ts = start_dt.replace(hour=0,  minute=0,  second=0,  microsecond=0).timestamp()
        end_ts   = end_dt.replace(  hour=23, minute=59, second=59, microsecond=999999).timestamp()

        # ── Build queryset ───────────────────────────────────────────
        # NOTE: no .only() here — we need block1_raw / block2_raw to decode.
        queryset = (
            SensorReading.objects
            .filter(timestamp__gte=start_ts, timestamp__lte=end_ts)
            .order_by('timestamp')
        )

        if unit_id is not None:
            queryset = queryset.filter(unit_id=unit_id.strip())

        # ── Decode each reading ──────────────────────────────────────
        results = [decode_reading(reading) for reading in queryset]

        return Response({
            "count":      len(results),
            "start_date": start_date_str,
            "end_date":   end_date_str,
            "unit_id":    unit_id,
            "results":    results,
        })
        

def _round_or_none(value, digits=1):
    """round() that lets an empty value stay empty (inverter rows have no voltage etc.)."""
    return round(value, digits) if value is not None else None


# ── Inverter live-state helpers ─────────────────────────────────────────
# Inverters post about once a minute, so they get a longer "no data" limit
# than meters. Some inverter gateways (ABB VSN700) stop sending completely at
# night. That is normal, so we show "sleeping" and not "stale/offline".
INVERTER_STALE_SECONDS = 150           # 2.5 minutes without data = no longer live
INVERTER_SLEEP_MAX_SECONDS = 18 * 3600  # a silent inverter is "sleeping" for up to 18 h
SLEEPING_MAX_KW = 0.05                 # last power at/below this = it went to sleep


def _is_inverter_unit(unit_id):
    """True when this unit_id has ever posted full inverter data (cached)."""
    key = f'is_inverter_unit_{unit_id}'
    result = cache.get(key)
    if result is None:
        result = InverterReading.objects.filter(unit_id=unit_id).exists()
        cache.set(key, result, 300 if result else 60)
    return result

def _seen_ts(reading):
    """
    When the SERVER received this reading. We use this (not the device's own
    timestamp) to decide online/stale, so a device with a wrong clock is not
    shown as offline while it is really sending data.
    """
    created = getattr(reading, 'created_at', None)
    return created.timestamp() if created else reading.timestamp


def _live_state(unit_id, last_ts, last_kw, now_ts=None, meter_limit=30):
    """Return 'online', 'stale' or 'sleeping' for one device from its newest reading."""
    now_ts = now_ts if now_ts is not None else timezone.now().timestamp()
    age = now_ts - last_ts
    if _is_inverter_unit(unit_id):
        if age <= INVERTER_STALE_SECONDS:
            return 'online'
        if age <= INVERTER_SLEEP_MAX_SECONDS and (last_kw is None or last_kw <= SLEEPING_MAX_KW):
            return 'sleeping'
        return 'stale'
    return 'online' if age <= meter_limit else 'stale'

class LatestReadingView(APIView):
    """Get the latest reading for live dashboard updates"""
    permission_classes = [IsAuthenticated] 
    
    def get(self, request):
        unit_id = (request.query_params.get('unit_id') or '100').strip()
        check_unit_access(request, unit_id)
        
        try:
            # Get the most recent reading
            latest = SensorReading.objects.filter(unit_id=unit_id).latest('timestamp')
            
            # Check if data is stale (older than 30 seconds)
            # online / stale / sleeping (inverters get a longer limit, see _live_state)
            live_state = _live_state(unit_id, _seen_ts(latest), latest.active_power_kw)

            # ── NEW: apply this DER's display scaling factor ──
            device = Device.objects.filter(unit_id=unit_id).first()
            scale = device.get_scale_factor() if device else 1.0
            
            data = {
                'voltage': _round_or_none(latest.voltage),
                'current': _round_or_none(latest.current),
                'active_power_kw': _round_or_none(latest.active_power_kw),
                'reactive_power_kvar': _round_or_none(latest.reactive_power_kvar),
                'apparent_power_kva': _round_or_none(latest.apparent_power_kva),
                'energy_today_kwh': _round_or_none(latest.energy_today_kwh),
                'timestamp': datetime.fromtimestamp(latest.timestamp, tz=EST).isoformat(),
                'status': live_state
            }
            
            serializer = LatestReadingSerializer(data)
            payload = dict(serializer.data)
            payload['is_inverter'] = _is_inverter_unit(unit_id)
            return Response(payload)
            
        except SensorReading.DoesNotExist:
            return Response({
                'status': 'offline',
                'message': 'No readings available'
            }, status=status.HTTP_404_NOT_FOUND)

class InverterLatestView(APIView):
    """
    Newest FULL inverter reading for one unit (state, AC, DC, MPPT, temperature).
    GET /api/inverter/latest/?unit_id=E108
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit_id = (request.query_params.get('unit_id') or '').strip()
        if not unit_id:
            return Response({'error': 'unit_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        check_unit_access(request, unit_id)

        r = InverterReading.objects.filter(unit_id=unit_id).order_by('-timestamp').first()
        if r is None:
            return Response({'status': 'offline', 'is_inverter': False,
                             'message': 'No inverter readings available'},
                            status=status.HTTP_404_NOT_FOUND)

        device = Device.objects.filter(unit_id=unit_id).first()
        scale = device.get_scale_factor() if device else 1.0

        def kw(watts, mult=1.0):
            return round(watts / 1000 * mult, 3) if watts is not None else None

        active_kw = kw(r.ac_active_power_w, scale)
        data = {
            'unit_id': unit_id,
            'is_inverter': True,
            'status': _live_state(unit_id, _seen_ts(r), active_kw),
            'timestamp': datetime.fromtimestamp(r.timestamp, tz=EST).isoformat(),
            'age_seconds': int(max(0, timezone.now().timestamp() - r.timestamp)),
            'operating_state': r.operating_state,
            # AC output (power and energy use the same display scale as the cards)
            'active_power_kw': active_kw,
            'apparent_power_kva': kw(r.ac_apparent_power_va, scale),
            'reactive_power_kvar': kw(r.ac_reactive_power_var, scale),
            'power_factor': r.power_factor,
            'grid_frequency_hz': r.grid_frequency_hz,
            'lifetime_energy_kwh': round(r.lifetime_energy_kwh * scale, 3) if r.lifetime_energy_kwh is not None else None,
            # AC voltage
            'voltage_a_n': r.ac_phase_a_voltage_v, 'voltage_b_n': r.ac_phase_b_voltage_v,
            'voltage_c_n': r.ac_phase_c_voltage_v,
            'voltage_a_b': r.ac_phase_ab_voltage_v, 'voltage_b_c': r.ac_phase_bc_voltage_v,
            'voltage_c_a': r.ac_phase_ca_voltage_v,
            # AC current
            'current_total_a': r.ac_total_current_a,
            'current_a': r.ac_phase_a_current_a, 'current_b': r.ac_phase_b_current_a,
            'current_c': r.ac_phase_c_current_a,
            # DC side and health
            'dc_voltage_v': r.dc_voltage_v,
            'dc_current_a': r.dc_total_current_a,
            'dc_power_kw': kw(r.dc_power_w),
            'cabinet_temperature_c': r.cabinet_temperature_c,
            'mppt_total_power_kw': kw(r.mppt_total_power_w),
            'mppt_inputs': r.mppt_inputs or [],
        }
        return Response(data)


from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
 
from django.db.models import Max

from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
 
class InverterRecentView(APIView):
    """
    History for the "Complete Readings" table when the selected DER is an inverter.
    GET /api/inverter/recent/?unit_id=E108&period=today|week|month
    GET /api/inverter/recent/?unit_id=E108&from=YYYY-MM-DD&to=YYYY-MM-DD
    Same period rules as RecentReadingsView: today/week = 5-minute rows,
    month or custom range = 30-minute rows, custom range up to 31 days.
    """
    permission_classes = [IsAuthenticated]
    MAX_RANGE_DAYS = 31

    @staticmethod
    def _mppt_text(inputs):
        parts = []
        for i, m in enumerate(inputs or []):
            if isinstance(m, dict):
                id_key = next((k for k in ('id', 'index', 'mppt', 'number') if k in m), None)
                label = f"MPPT{m[id_key] if id_key else i + 1}"
                vals = ' '.join(f"{str(k).replace('_', ' ')} {v}" for k, v in m.items() if k != id_key)
                parts.append(f"{label}: {vals}")
            else:
                parts.append(f"MPPT{i + 1}: {m}")
        return ' | '.join(parts)

    def _row(self, r, scale):
        dt = datetime.fromtimestamp(r.timestamp, tz=EST)

        def kw(watts, mult=1.0):
            return round(watts / 1000 * mult, 3) if watts is not None else None

        return {
            'date': dt.strftime('%Y-%m-%d'), 'time': dt.strftime('%H:%M:%S'),
            'state': r.operating_state,
            'kw': kw(r.ac_active_power_w, scale),
            'kva': kw(r.ac_apparent_power_va, scale),
            'kvar': kw(r.ac_reactive_power_var, scale),
            'pf': r.power_factor, 'freq': r.grid_frequency_hz,
            'v_an': r.ac_phase_a_voltage_v, 'v_bn': r.ac_phase_b_voltage_v, 'v_cn': r.ac_phase_c_voltage_v,
            'v_ab': r.ac_phase_ab_voltage_v, 'v_bc': r.ac_phase_bc_voltage_v, 'v_ca': r.ac_phase_ca_voltage_v,
            'i_total': r.ac_total_current_a,
            'i_a': r.ac_phase_a_current_a, 'i_b': r.ac_phase_b_current_a, 'i_c': r.ac_phase_c_current_a,
            'dc_v': r.dc_voltage_v, 'dc_a': r.dc_total_current_a, 'dc_kw': kw(r.dc_power_w),
            'mppt_kw': kw(r.mppt_total_power_w),
            'temp_c': r.cabinet_temperature_c,
            'energy': round(r.lifetime_energy_kwh * scale, 2) if r.lifetime_energy_kwh is not None else None,
            'modbus': r.modbus_address,
            'mppt_text': self._mppt_text(r.mppt_inputs),
        }

    def get(self, request):
        unit_id = (request.query_params.get('unit_id') or '').strip()
        if not unit_id:
            return Response({'error': 'unit_id is required'}, status=400)
        check_unit_access(request, unit_id)

        period = (request.query_params.get('period') or '').strip()
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        device = Device.objects.filter(unit_id=unit_id).first()
        scale = device.get_scale_factor() if device else 1.0
        now_local = datetime.now(EST)
        start_dt = end_dt = None
        stride_seconds = 300

        if from_date and to_date:
            try:
                start_dt = datetime.strptime(from_date, '%Y-%m-%d').replace(
                    hour=0, minute=0, second=0, microsecond=0, tzinfo=EST)
                end_dt = datetime.strptime(to_date, '%Y-%m-%d').replace(
                    hour=23, minute=59, second=59, microsecond=999999, tzinfo=EST)
            except ValueError:
                return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)
            range_days = (end_dt.date() - start_dt.date()).days + 1
            if range_days > self.MAX_RANGE_DAYS:
                return Response({
                    'error': f'Date range too large ({range_days} days). '
                             f'Maximum allowed range is {self.MAX_RANGE_DAYS} days (about one month).',
                    'max_range_days': self.MAX_RANGE_DAYS,
                }, status=400)
            stride_seconds = 1800
        elif period == 'today':
            start_dt = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = now_local
        elif period == 'week':
            start_dt = (now_local - timedelta(days=(now_local.weekday() + 1) % 7)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            end_dt = now_local
        elif period == 'month':
            start_dt = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_dt = now_local
            stride_seconds = 1800

        base_qs = InverterReading.objects.filter(unit_id=unit_id)

        if start_dt and end_dt:
            ranged_qs = base_qs.filter(timestamp__gte=start_dt.timestamp(),
                                       timestamp__lte=end_dt.timestamp()).order_by('timestamp')
            picked, last_ts = [], None
            for r in ranged_qs.iterator(chunk_size=2000):
                if last_ts is None or (r.timestamp - last_ts) >= stride_seconds:
                    picked.append(r)
                    last_ts = r.timestamp
            picked.reverse()                      # newest first, like the meter table
            meta = {'mode': 'range', 'rows_returned': len(picked),
                    'sampling_interval_seconds': stride_seconds,
                    'downsampled': stride_seconds > 300}
        else:
            try:
                limit = max(1, min(int(request.query_params.get('limit', 20)), 200))
            except (TypeError, ValueError):
                limit = 20
            picked, last_ts = [], None
            for r in base_qs.order_by('-timestamp').iterator(chunk_size=500):
                if last_ts is None or (last_ts - r.timestamp) >= 300:
                    picked.append(r)
                    last_ts = r.timestamp
                    if len(picked) >= limit:
                        break
            meta = {'mode': 'legacy', 'rows_returned': len(picked)}

        return Response({'rows': [self._row(r, scale) for r in picked], 'meta': meta})


from django.db.models import Max
from rest_framework.views import APIView
from rest_framework.response import Response
 
from .models import SensorReading, Device
from .serializers import SummaryStatsSerializer
 
from collections import defaultdict
from datetime import datetime, timedelta
from django.db.models import Max
from rest_framework.views import APIView
from rest_framework.response import Response
# ============================================================
# CLIENT-PROVIDED HISTORICAL BASELINE VALUES
# We have NO meter readings before 23 May 2026 in the database.
# The client gave us these cumulative meter readings so we can
# calculate Energy This Year and Lifetime Energy correctly.
#
# IMPORTANT — ODOMETER OFFSET
# ---------------------------------------------------------------
# The raw meter reading (energy_today_kwh) is NOT necessarily the
# true cumulative site energy. When a meter is swapped (or its
# odometer is reset/replaced) the device carries a per-device
# Device.odometer_offset that re-aligns the raw counter to the
# real-world running total.
#
#     Actual cumulative site energy = energy_today_kwh + Device.odometer_offset
#
# The client-provided baselines below (e.g. 781,934 at Dec 31 2025)
# are expressed in this ACTUAL cumulative space, so any metric that
# compares against them must first add the offset to the raw reading.
# ============================================================
CLIENT_BASELINES = {
    '100': {
        # ACTUAL cumulative meter reading (raw + odometer_offset) at
        # 23:59 on 31 Dec 2025 — the anchor for "Energy This Year".
        'year_start_cumulative': 781_934,    # kWh
        # Revenue earned BEFORE 1 Jan 2026 (used only by the audit endpoints).
        'pre_period_revenue':    96_015.00,  # USD
    },
    # '101': { ... }   ← add here ONLY if a unit also lacks early DB history
}


# Backward-compatibility aliases so any other reference still resolves.
CLIENT_BASELINE_DEC31_2025        = CLIENT_BASELINES['100']['year_start_cumulative']
CLIENT_LIFETIME_REVENUE_JAN1_2026 = CLIENT_BASELINES['100']['pre_period_revenue']

# Cumulative meter reading at end of day 30 April 2026.
# Useful for cross-checking monthly calculations.
CLIENT_BASELINE_APR30_2026 = 796_095   # kWh cumulative
 
# Cumulative meter reading at end of day 31 May 2026.
# Useful for cross-checking monthly calculations.
CLIENT_BASELINE_MAY31_2026 = 801_135   # kWh cumulative
 
 
# Timezone for all local-time calculations (the site is in New York)
SITE_TZ = ZoneInfo("America/New_York")
 
BASELINE_STALENESS_LIMIT_SECONDS = 48 * 3600  # 24 hours

def get_baseline_reading(qs, anchor_ts, max_age_seconds=BASELINE_STALENESS_LIMIT_SECONDS):
    """
    Returns (reading, is_stale, age_hours) for the last reading at/before
    anchor_ts within this queryset.

    reading   : the SensorReading found (or None if nothing exists at all,
                before OR after the anchor)
    is_stale  : True if the found reading is older than max_age_seconds
                before the anchor -- callers should treat the result as
                incomplete rather than a trustworthy number.
    age_hours : how many hours before the anchor the reading actually is
                (0 if found reading is at/after anchor via fallback)
    """
    r = qs.filter(timestamp__lte=anchor_ts).order_by('-timestamp').first()
    if r is not None:
        age_seconds = anchor_ts - r.timestamp
        is_stale = age_seconds > max_age_seconds
        return r, is_stale, round(age_seconds / 3600, 1)

    # No reading before the anchor at all -- fall forward to the earliest
    # reading after it (same fallback behavior as before), but this is
    # ALWAYS treated as stale/incomplete since it's not a real baseline.
    r2 = qs.filter(timestamp__gt=anchor_ts).order_by('timestamp').first()
    if r2 is not None:
        return r2, True, 0.0
    return None, True, None


MONTH_END_OVERRIDES = {
    ('100', '2026-05'): 5040.0,   # May 2026 ends at 5040 (client-confirmed bill)
}

MONTH_START_OVERRIDES = {
    # August 2026 — these units' sensor data only starts mid-month
    # (first reading Aug 10/11), so the natural "reading at start of
    # month" lookup would find nothing and undercount the bill.
    # Client-confirmed end-of-July readings used as the August
    # starting point instead.
    ('104', '2026-08'): 1501691.0,   # Civil Court House
    ('102', '2026-08'): 2333794.0,   # Criminal Court Complex
    ('105', '2026-08'): 2233239.0,   # Mays Landing Justice Facility
}



def get_month_start_baseline(unit_id, year, month, qs, fallback_dt):
    """
    Returns (value, is_override, is_stale) for a month's STARTING reading.
    Checks MONTH_START_OVERRIDES first; falls back to the normal
    get_baseline_reading() lookup if no override exists for this month.
    """
    key = (str(unit_id), f"{year:04d}-{month:02d}")
    if key in MONTH_START_OVERRIDES:
        return MONTH_START_OVERRIDES[key], True, False

    r, is_stale, age_h = get_baseline_reading(qs, fallback_dt.timestamp())
    val = r.energy_today_kwh if r else None
    return val, False, is_stale


def get_month_end_baseline(unit_id, year, month, qs, fallback_dt):
    """
    Returns (value, is_override, is_stale) for a month's ENDING reading.
    Mirrors get_month_start_baseline() but for MONTH_END_OVERRIDES.
    """
    key = (str(unit_id), f"{year:04d}-{month:02d}")
    if key in MONTH_END_OVERRIDES:
        return MONTH_END_OVERRIDES[key], True, False

    r, is_stale, age_h = get_baseline_reading(qs, fallback_dt.timestamp())
    val = r.energy_today_kwh if r else None
    return val, False, is_stale



 
def get_per_unit_cost(device):
    """
    Return the device's per-kWh cost as a float.
    Revenue formula:  USD = kWh x Device.per_unit_cost
    If device is missing OR per_unit_cost is null, the cost is 0.
    """
    if device is not None and device.per_unit_cost is not None:
        return float(device.per_unit_cost)
    return 0.0


def _resolve_year_baseline(unit_id, qs, current_reading, offset, now_local):
    """
    Return the ACTUAL-cumulative kWh baseline to subtract for "Energy This Year".
 
    • Units in CLIENT_BASELINES (e.g. 100): use the client-provided value.
    • EVERY OTHER UNIT: derive from the DB — the cumulative reading at or
      before 00:00 on 1 Jan of the current year. If the meter has no data that
      far back, fall back to its earliest reading (so the year starts from the
      first sample we ever stored for that unit).
 
    Returned in ACTUAL-cumulative space (raw + offset) so the caller can do
    `current_cumulative - baseline`. The offset cancels for DB-derived values,
    which is exactly what we want.
    """
    cfg = CLIENT_BASELINES.get(str(unit_id))
    if cfg is not None:
        return cfg['year_start_cumulative']           # already actual-cumulative
 
    # DB-derived: anchor = 1 Jan of the current year, local midnight.
    year_start = now_local.replace(month=1, day=1, hour=0,
                                   minute=0, second=0, microsecond=0)
    anchor_ts = year_start.timestamp()
 
    r = qs.filter(timestamp__lte=anchor_ts).order_by('-timestamp').first()
    if r is None:
        r = qs.order_by('timestamp').first()          # earliest reading we have
    baseline_raw = r.energy_today_kwh if r is not None else current_reading
    return baseline_raw + offset                       # raw → actual cumulative
 
 
def _compute_unit_summary(unit_id):
    unit_id = str(unit_id).strip()
    device  = Device.objects.filter(unit_id=unit_id).first()
    offset  = float(device.odometer_offset or 0) if device else 0.0

    qs = SensorReading.objects.filter(unit_id=unit_id, energy_today_kwh__isnull=False)
    latest = qs.order_by('-timestamp').first()
    if latest is None:
        return None

    current_reading    = latest.energy_today_kwh
    current_cumulative = current_reading + offset

    now_local      = datetime.now(SITE_TZ)
    today_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    last_sunday    = today_midnight - timedelta(days=(now_local.weekday() + 1) % 7)
    month_start    = today_midnight.replace(day=1)

    def db_baseline_checked(anchor_dt):
        r, is_stale, age_h = get_baseline_reading(qs, anchor_dt.timestamp())
        if r is None:
            return current_reading, True, None
        return r.energy_today_kwh, is_stale, age_h

    today_val, today_incomplete, today_age = db_baseline_checked(today_midnight)
    week_val,  week_incomplete,  week_age  = db_baseline_checked(last_sunday)

    month_val, is_override, month_stale = get_month_start_baseline(
        unit_id, month_start.year, month_start.month, qs, month_start
    )
    if month_val is None:
        month_val, month_incomplete = current_reading, True
    else:
        month_incomplete = month_stale and not is_override

    today_kwh = max(0.0, current_reading - today_val)
    week_kwh  = max(0.0, current_reading - week_val)
    month_kwh = max(0.0, current_reading - month_val)

    year_baseline = _resolve_year_baseline(unit_id, qs, current_reading, offset, now_local)
    year_kwh      = max(0.0, current_cumulative - year_baseline)
    lifetime_kwh  = current_cumulative

    peak = (qs.filter(timestamp__gte=today_midnight.timestamp())
              .aggregate(Max('active_power_kw'))['active_power_kw__max']) or 0.0

    as_of   = now_local.date()
    cost_bd = get_escalated_cost_breakdown(device, as_of)

    return {
        'unit_id': unit_id, 'device': device,
        'today_kwh': today_kwh, 'week_kwh': week_kwh, 'month_kwh': month_kwh,
        'today_incomplete': today_incomplete,
        'week_incomplete': week_incomplete,
        'month_incomplete': month_incomplete,
        'year_kwh': year_kwh, 'lifetime_kwh': lifetime_kwh, 'peak_demand_kw': peak,
        'per_unit_cost':  float(device.per_unit_cost) if (device and device.per_unit_cost is not None) else 0.0,
        'escalated_cost': cost_bd['escalated_cost'],
        'escalator_pct':  cost_bd['escalator_pct'],
        'effective_date': cost_bd['effective_date'],
        'years_elapsed':  cost_bd['years_elapsed'],
        'today_usd':    calculate_revenue(device, today_kwh,    as_of, metric='today'),
        'week_usd':     calculate_revenue(device, week_kwh,     as_of, metric='week'),
        'month_usd':    calculate_revenue(device, month_kwh,    as_of, metric='month'),
        'year_usd':     calculate_revenue(device, year_kwh,     as_of, metric='year'),
        'lifetime_usd': calculate_revenue(device, lifetime_kwh, as_of, metric='lifetime'),
    }
    
DEVICE_STALE_SECONDS = 60              # matches LatestReadingView's own cutoff
DEVICE_OFFLINE_SECONDS = 24 * 3600     # no reading in 24h -> treat as offline
COMPANY_OVERVIEW_CACHE_SECONDS = 120   # re-derive at most every 2 min
# GENERATION_CATEGORIES removed — every device category present in a
# microgrid now counts toward Current Gen / Lifetime Gen for that microgrid.


def _compute_company_overview(company):
    """
    Aggregate Status / Alarms / Current Gen / Lifetime Gen / Lifetime Rev
    for ONE microgrid, from ALL of its devices regardless of category.

    IMPORTANT: "Current Gen" only sums devices whose latest reading is
    still fresh (within DEVICE_STALE_SECONDS). A device that hasn't
    reported in hours/days must NOT contribute its old active_power_kw
    to a "current" figure — that would silently mix live and frozen
    numbers and produce a misleading total.
    """
    cache_key = f'mg_overview_company_{company.id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    devices = list(company.devices.all())
    now_ts = timezone.now().timestamp()

    online = stale = offline = sleeping = 0
    current_gen_kw = 0.0
    lifetime_gen_kwh = 0.0
    lifetime_rev_usd = 0.0

    for device in devices:
        latest = SensorReading.objects.filter(unit_id=device.unit_id).order_by('-timestamp').first()

        is_fresh = False
        if latest is None:
            offline += 1
        else:
            age = now_ts - _seen_ts(latest)
            if age > DEVICE_OFFLINE_SECONDS:
                offline += 1
            else:
                state = _live_state(device.unit_id, _seen_ts(latest), latest.active_power_kw,
                                    now_ts, meter_limit=DEVICE_STALE_SECONDS)
                if state == 'online':
                    online += 1
                    is_fresh = True
                elif state == 'sleeping':
                    sleeping += 1      # inverter asleep at night: not a fault
                else:
                    stale += 1

        # Current Gen: ONLY devices reporting live (fresh) right now.
        # A stale/offline device's last known power value is not "current".
        if is_fresh and latest.active_power_kw is not None:
            current_gen_kw += latest.active_power_kw          # ← CHANGE THIS LINE ONLY

        # Lifetime Gen / Lifetime Rev: unaffected by freshness — these are
        # cumulative meter deltas, correct regardless of when last polled.
        summary = _compute_unit_summary(device.unit_id)
        if summary:
            lifetime_gen_kwh += summary['lifetime_kwh']
            lifetime_rev_usd += summary['lifetime_usd']

    total = len(devices)
    awake = total - sleeping          # sleeping inverters do not count as a problem
    if total == 0:
        status = 'no_devices'
    elif awake == 0:
        status = 'sleeping'
    elif online == awake:
        status = 'online'
    elif offline == awake:
        status = 'offline'
    else:
        status = 'partial'

    alarm_count = (stale + offline) if total else 0

    overview = {
        'status': status,
        'online': online, 'stale': stale, 'offline': offline, 'sleeping': sleeping, 'total_devices': total,
        'alarm_count': alarm_count,
        'current_gen_kw': round(current_gen_kw, 1),
        'lifetime_gen_kwh': round(lifetime_gen_kwh, 1),
        'lifetime_rev_usd': round(lifetime_rev_usd, 2),
    }
    cache.set(cache_key, overview, COMPANY_OVERVIEW_CACHE_SECONDS)
    return overview

def _coincident_peak(unit_ids, today_midnight_ts):
    rows = (SensorReading.objects
            .filter(unit_id__in=unit_ids,
                    timestamp__gte=today_midnight_ts,
                    active_power_kw__isnull=False)
            .values_list('unit_id', 'timestamp', 'active_power_kw'))

    per_unit_minute = {}
    for uid, ts, kw in rows.iterator():
        key = (uid, int(ts // 60))
        per_unit_minute[key] = max(per_unit_minute.get(key, kw), kw)

    minute_totals = defaultdict(float)
    for (uid, minute), kw in per_unit_minute.items():
        minute_totals[minute] += kw
    return max(minute_totals.values()) if minute_totals else 0.0
 
class SummaryStatsView(APIView):
    permission_classes = [IsAuthenticated]
 
    def _single_payload(self, s):
        """Original Unit-100-compatible response shape for a single unit."""
        return {
            'today_kwh':      round(s['today_kwh'],     1),
            'week_kwh':       round(s['week_kwh'],      1),
            'month_kwh':      round(s['month_kwh'],     1),
            'year_kwh':       round(s['year_kwh'],      1),
            'lifetime_kwh':   round(s['lifetime_kwh'],  1),
            'peak_demand_kw': round(s['peak_demand_kw'],1),
            'per_unit_cost':  s['per_unit_cost'],
            'escalated_cost': s['escalated_cost'],
            'escalator_pct':  s['escalator_pct'],
            'effective_date': s['effective_date'].isoformat() if s['effective_date'] else None,
            'years_elapsed':  s['years_elapsed'],
            'today_usd':      s['today_usd'],
            'today_incomplete': s['today_incomplete'],
            'week_incomplete':  s['week_incomplete'],
            'month_incomplete': s['month_incomplete'],
            'week_usd':       s['week_usd'],      # NEW: home page no longer reconstructs this
            'month_usd':      s['month_usd'],
            'year_usd':       s['year_usd'],
            'lifetime_usd':   s['lifetime_usd'],
        }
 
    def get(self, request):
        # ── Resolve target units: ?unit_ids= wins, else single ?unit_id= ──
        unit_ids_param = request.query_params.get('unit_ids')
        if unit_ids_param:
            unit_ids = [u.strip() for u in unit_ids_param.split(',') if u.strip()]
        else:
            unit_ids = [(request.query_params.get('unit_id') or '100').strip()]
        unit_ids = [u for u in unit_ids if request.user.can_access_unit(u)]
        if not unit_ids:
            return Response({'today_kwh': 0.0, 'week_kwh': 0.0, 'month_kwh': 0.0,
                              'year_kwh': 0.0, 'lifetime_kwh': 0.0, 'peak_demand_kw': 0.0,
                              'per_unit_cost': 0.0, 'today_usd': 0.0, 'week_usd': 0.0,
                              'month_usd': 0.0, 'year_usd': 0.0, 'lifetime_usd': 0.0})
 
        per_unit = [s for s in (_compute_unit_summary(u) for u in unit_ids) if s]
 
        EMPTY = {
            'today_kwh': 0.0, 'week_kwh': 0.0, 'month_kwh': 0.0, 'year_kwh': 0.0,
            'lifetime_kwh': 0.0, 'peak_demand_kw': 0.0, 'per_unit_cost': 0.0,
            'today_usd': 0.0, 'week_usd': 0.0, 'month_usd': 0.0,
            'year_usd': 0.0, 'lifetime_usd': 0.0,
        }
        if not per_unit:
            return Response(EMPTY)
 
        # ── Single unit & no explicit unit_ids → original behaviour ──
        if len(per_unit) == 1 and not unit_ids_param:
            return Response(self._single_payload(per_unit[0]))
 
        # ── Aggregate: SUM kWh, SUM per-device revenue, coincident peak ──
        now_local      = datetime.now(SITE_TZ)
        today_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        ids            = [s['unit_id'] for s in per_unit]
 
        return Response({
            'today_kwh':      round(sum(s['today_kwh']    for s in per_unit), 1),
            'week_kwh':       round(sum(s['week_kwh']     for s in per_unit), 1),
            'month_kwh':      round(sum(s['month_kwh']    for s in per_unit), 1),
            'year_kwh':       round(sum(s['year_kwh']     for s in per_unit), 1),
            'lifetime_kwh':   round(sum(s['lifetime_kwh'] for s in per_unit), 1),
            'peak_demand_kw': round(_coincident_peak(ids, today_midnight.timestamp()), 1),
            'today_usd':      round(sum(s['today_usd']    for s in per_unit), 2),
            'week_usd':       round(sum(s['week_usd']     for s in per_unit), 2),
            'month_usd':      round(sum(s['month_usd']    for s in per_unit), 2),
            'year_usd':       round(sum(s['year_usd']     for s in per_unit), 2),
            'lifetime_usd':   round(sum(s['lifetime_usd'] for s in per_unit), 2),
            'unit_count':     len(per_unit),
            # Optional per-device breakdown for tooltips / debugging.
            'per_unit': [{
                'unit_id': s['unit_id'],
                'today_kwh': round(s['today_kwh'], 1),
                'year_kwh':  round(s['year_kwh'], 1),
                'lifetime_kwh': round(s['lifetime_kwh'], 1),
                'today_usd': s['today_usd'], 'month_usd': s['month_usd'],
                'year_usd':  s['year_usd'],  'lifetime_usd': s['lifetime_usd'],
            } for s in per_unit],
        })
 
 
class EnergyVerificationView(APIView):
    """Energy (kWh) verification endpoint. GET /api/readings/verify/?unit_id=100"""
    permission_classes = [IsAuthenticated]   

    def get(self, request):
        unit_id = (request.query_params.get('unit_id') or '100').strip()
        check_unit_access(request, unit_id) 
        device = Device.objects.filter(unit_id=unit_id).first()
        offset = float(device.odometer_offset or 0) if device else 0.0

        CLIENT_DEC31_2025 = 781_934
        CLIENT_APR30_2026 = 796_095
        CLIENT_MAY31_2026 = 801_135
        CLIENT_LIFETIME_REVENUE = 96_015.00

        qs = SensorReading.objects.filter(
            unit_id=unit_id, energy_today_kwh__isnull=False
        ).order_by('timestamp')
        latest = qs.order_by('-timestamp').first()
        if latest is None:
            return Response({
                "error": "No readings found in database for this unit",
                "unit_id": unit_id,
                "suggestion": "Check that the data collector is running and sending readings"
            }, status=404)

        raw_meter_reading = latest.energy_today_kwh
        current_reading = raw_meter_reading
        current_cumulative_reading = raw_meter_reading + offset
        current_ts_unix = latest.timestamp
        current_ts_local = datetime.fromtimestamp(current_ts_unix, tz=SITE_TZ).isoformat()
        current_db_id = latest.id

        now_local = datetime.now(SITE_TZ)
        today_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        last_sunday = today_midnight - timedelta(days=(now_local.weekday() + 1) % 7)
        month_start = today_midnight.replace(day=1)

        def find_db_baseline(anchor_dt, label):
            anchor_ts = anchor_dt.timestamp()
            reading = qs.filter(timestamp__lte=anchor_ts).order_by('-timestamp').first()
            if reading:
                return {
                    "db_record_id": reading.id,
                    "raw_timestamp_unix": reading.timestamp,
                    "timestamp_local_est": datetime.fromtimestamp(reading.timestamp, tz=SITE_TZ).isoformat(),
                    "meter_reading_kwh": reading.energy_today_kwh,
                    "source": "DATABASE -- reading at or before anchor",
                    "anchor_requested": anchor_dt.isoformat(),
                    "anchor_unix": anchor_ts,
                    "why_selected": f"Last reading on/before {label} (anchor={anchor_dt.isoformat()})",
                    "found": True,
                }
            reading_after = qs.filter(timestamp__gt=anchor_ts).order_by('timestamp').first()
            if reading_after:
                return {
                    "db_record_id": reading_after.id,
                    "raw_timestamp_unix": reading_after.timestamp,
                    "timestamp_local_est": datetime.fromtimestamp(reading_after.timestamp, tz=SITE_TZ).isoformat(),
                    "meter_reading_kwh": reading_after.energy_today_kwh,
                    "source": "DATABASE -- earliest reading AFTER anchor (no earlier data)",
                    "anchor_requested": anchor_dt.isoformat(),
                    "anchor_unix": anchor_ts,
                    "why_selected": f"No reading existed before {label}. Used earliest available reading.",
                    "found": True,
                }
            return {
                "db_record_id": None,
                "meter_reading_kwh": current_reading,
                "source": "FALLBACK -- using current reading (no baseline found)",
                "found": False,
            }

        def calc_result(current, baseline):
            return round(max(0.0, current - baseline), 2)

        today_baseline_info = find_db_baseline(today_midnight, "today midnight")
        today_baseline_val = today_baseline_info["meter_reading_kwh"]
        today_result = calc_result(current_reading, today_baseline_val)
        energy_today = {
            "metric": "Energy This Period (Today)",
            "formula": "Current Meter Reading - Reading at Today's Midnight",
            "current_meter_reading": current_reading,
            "baseline_reading": today_baseline_val,
            "baseline_source": today_baseline_info["source"],
            "anchor_time": today_midnight.isoformat(),
            "calculation": f"{current_reading} - {today_baseline_val} = {today_result}",
            "final_result_kwh": today_result,
            "offset_note": "Period delta of two raw readings -- odometer offset cancels.",
        }

        week_baseline_info = find_db_baseline(last_sunday, "last Sunday midnight")
        week_baseline_val = week_baseline_info["meter_reading_kwh"]
        week_result = calc_result(current_reading, week_baseline_val)
        energy_week = {
            "metric": "Energy This Week",
            "formula": "Current Meter Reading - Reading at Last Sunday Midnight",
            "current_meter_reading": current_reading,
            "baseline_reading": week_baseline_val,
            "baseline_source": week_baseline_info["source"],
            "anchor_time": last_sunday.isoformat(),
            "calculation": f"{current_reading} - {week_baseline_val} = {week_result}",
            "final_result_kwh": week_result,
            "offset_note": "Period delta of two raw readings -- odometer offset cancels.",
        }

        month_baseline_info = find_db_baseline(month_start, "1st of month midnight")
        month_baseline_val = month_baseline_info["meter_reading_kwh"]
        month_result = calc_result(current_reading, month_baseline_val)
        energy_month = {
            "metric": "Energy This Month",
            "formula": "Current Meter Reading - Reading at 1st of Month Midnight",
            "current_meter_reading": current_reading,
            "baseline_reading": month_baseline_val,
            "baseline_source": month_baseline_info["source"],
            "anchor_time": month_start.isoformat(),
            "calculation": f"{current_reading} - {month_baseline_val} = {month_result}",
            "final_result_kwh": month_result,
            "offset_note": "Period delta of two raw readings -- odometer offset cancels.",
        }

        year_baseline_val = _resolve_year_baseline(unit_id, qs, current_reading, offset, now_local)
        year_result = calc_result(current_cumulative_reading, year_baseline_val)

        energy_year = {
            "metric": "Energy This Year",
            "formula": "(Current Meter Reading + odometer_offset) - year_baseline",
            "raw_meter_reading": raw_meter_reading,
            "odometer_offset": offset,
            "current_cumulative_reading": current_cumulative_reading,
            "baseline_reading": year_baseline_val,
            "baseline_source": "CLIENT-PROVIDED VALUE -- no DB readings before 23 May 2026",
            "calculation": f"({raw_meter_reading} + {offset}) - {year_baseline_val} = {year_result}",
            "final_result_kwh": year_result,
        }

        lifetime_kwh = current_cumulative_reading
        energy_lifetime = {
            "metric": "Lifetime Energy",
            "formula": "Current Meter Reading + odometer_offset",
            "raw_meter_reading": raw_meter_reading,
            "odometer_offset": offset,
            "current_cumulative_reading": current_cumulative_reading,
            "calculation": f"Lifetime = {raw_meter_reading} + {offset} = {lifetime_kwh} kWh",
            "final_result_kwh": lifetime_kwh,
            "cross_check": {
                "client_dec31_2025_reading": CLIENT_DEC31_2025,
                "client_apr30_2026_reading": CLIENT_APR30_2026,
                "client_may31_2026_reading": CLIENT_MAY31_2026,
                "energy_added_since_jan1": round(current_cumulative_reading - CLIENT_DEC31_2025, 2),
            }
        }

        _cost_bd = get_escalated_cost_breakdown(device, now_local.date())
        rate_per_kwh = float(request.query_params.get('rate', _cost_bd['escalated_cost']))
        energy_since_jan1 = round(max(0.0, current_cumulative_reading - year_baseline_val), 2)
        revenue_since_jan1 = round(energy_since_jan1 * rate_per_kwh, 2)
        _cfg = CLIENT_BASELINES.get(str(unit_id))
        pre_period_rev = _cfg['pre_period_revenue'] if _cfg else 0.0
        lifetime_revenue = round(pre_period_rev + revenue_since_jan1, 2)

        lifetime_revenue_info = {
            "metric": "Lifetime Revenue",
            "formula": "Pre-2026 Revenue + (Energy Since Jan 1, 2026 x Rate per kWh)",
            "pre_2026_revenue_usd": CLIENT_LIFETIME_REVENUE,
            "energy_since_jan1_kwh": energy_since_jan1,
            "rate_per_kwh_usd": rate_per_kwh,
            "revenue_since_jan1_usd": revenue_since_jan1,
            "final_lifetime_revenue_usd": lifetime_revenue,
            "note": "Adjust ?rate=0.14 in the URL to use a different kWh price",
        }

        oldest = qs.order_by('timestamp').first()
        total_records = qs.count()
        db_overview = {
            "total_records_in_db": total_records,
            "earliest_record": {
                "db_id": oldest.id if oldest else None,
                "local_est": datetime.fromtimestamp(oldest.timestamp, tz=SITE_TZ).isoformat() if oldest else None,
                "meter_reading": oldest.energy_today_kwh if oldest else None,
            },
            "latest_record": {
                "db_id": current_db_id,
                "local_est": current_ts_local,
                "raw_meter_reading": raw_meter_reading,
                "odometer_offset": offset,
                "current_cumulative_reading": current_cumulative_reading,
            },
        }

        timezone_info = {
            "site_timezone": "America/New_York",
            "server_utc_now": datetime.now(dt_timezone.utc).isoformat(),
            "site_local_now": now_local.isoformat(),
        }

        return Response({
            "verification_report": {
                "title": "Energy Calculation Verification Report",
                "unit_id": unit_id,
                "generated_at": now_local.isoformat(),
            },
            "odometer_offset_info": {
                "device_found": device is not None,
                "odometer_offset": offset,
                "raw_meter_reading": raw_meter_reading,
                "current_cumulative_reading": current_cumulative_reading,
            },
            "client_provided_values": {
                "dec_31_2025_meter_reading": CLIENT_DEC31_2025,
                "apr_30_2026_meter_reading": CLIENT_APR30_2026,
                "may_31_2026_meter_reading": CLIENT_MAY31_2026,
                "lifetime_revenue_jan1_2026_usd": CLIENT_LIFETIME_REVENUE,
            },
            "metrics": {
                "energy_today": energy_today,
                "energy_week": energy_week,
                "energy_month": energy_month,
                "energy_year": energy_year,
                "lifetime_energy": energy_lifetime,
                "lifetime_revenue": lifetime_revenue_info,
            },
            "database_overview": db_overview,
            "timezone_audit": timezone_info,
        })
 
# ============================================================
# SHARED KWH HELPER (read-only) -- used by RevenueVerificationView
# so the revenue audit reuses the SAME kWh formulas as SummaryStatsView
# without altering that view. Returns None when no readings exist.
# ============================================================
def compute_kwh_summary(unit_id):
    device = Device.objects.filter(unit_id=unit_id).first()
    offset = float(device.odometer_offset or 0) if device else 0.0

    qs = SensorReading.objects.filter(unit_id=unit_id, energy_today_kwh__isnull=False)
    latest = qs.order_by('-timestamp').first()
    if latest is None:
        return None

    current_reading = latest.energy_today_kwh
    current_cumulative = current_reading + offset

    now_local = datetime.now(SITE_TZ)
    today_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today_midnight.replace(day=1)

    def db_baseline(anchor_dt):
        anchor_ts = anchor_dt.timestamp()
        r = qs.filter(timestamp__lte=anchor_ts).order_by('-timestamp').first()
        if r is not None:
            return r.energy_today_kwh
        r2 = qs.filter(timestamp__gt=anchor_ts).order_by('timestamp').first()
        return r2.energy_today_kwh if r2 is not None else current_reading

    today_kwh    = max(0.0, current_reading - db_baseline(today_midnight))
    month_kwh    = max(0.0, current_reading - db_baseline(month_start))
    year_baseline = _resolve_year_baseline(unit_id, qs, current_reading, offset, now_local)
    year_kwh      = max(0.0, current_cumulative - year_baseline)
    lifetime_kwh = current_reading + offset

    return {
        'device': device,
        'offset': offset,
        'current_reading': current_reading,
        'current_cumulative': current_cumulative,
        'today_kwh': today_kwh,
        'month_kwh': month_kwh,
        'year_kwh': year_kwh,
        'lifetime_kwh': lifetime_kwh,
    }
 
class RevenueVerificationView(APIView):
    """
    Revenue (USD) verification endpoint -- mirror of EnergyVerificationView.
    GET /api/readings/revenue-verify/?unit_id=100
 
    Revenue formula:  USD = kWh x Device.per_unit_cost  (0 if cost is null)
    Per metric (Today, Month, Year, Lifetime):
        unit_id, device_id, per_unit_cost, kwh_value_used,
        formula_used, calculation_breakdown, final_usd_value
    """
    permission_classes = [IsAuthenticated]
 
    def get(self, request):
        unit_id = (request.query_params.get('unit_id') or '100').strip()
        check_unit_access(request, unit_id)     
        summary = compute_kwh_summary(unit_id)
        if summary is None:
            return Response({
                "error": "No readings found in database for this unit",
                "unit_id": unit_id,
                "suggestion": "Check that the data collector is running and sending readings"
            }, status=404)
 
        device = summary['device']
        device_id = device.id if device else None
        per_unit_cost = get_per_unit_cost(device)
        cost_is_null = (device is None) or (device.per_unit_cost is None)
 
        cost_bd = get_escalated_cost_breakdown(device, datetime.now(SITE_TZ).date())
        escalated_cost = cost_bd['escalated_cost']

        def build_metric(name, kwh, metric):
            kwh_used = round(kwh, 2)
            rate_used = cost_bd['escalated_cost']
            usd = round(kwh * rate_used, 2)
            return {
                "metric": name,
                "unit_id": unit_id,
                "device_id": device_id,
                "base_cost": cost_bd['base_cost'],
                "escalator_pct": cost_bd['escalator_pct'],
                "effective_date": cost_bd['effective_date'].isoformat() if cost_bd['effective_date'] else None,
                "years_elapsed": cost_bd['years_elapsed'],
                "escalated_cost": cost_bd['escalated_cost'],
                "rate_applied": rate_used,
                "escalator_applied": True,
                "kwh_value_used": kwh_used,
                "formula_used": "USD = kWh x escalated_cost (applies to every period)",
                "calculation_breakdown": f"{kwh_used} kWh x ${rate_used:.4f}/kWh = ${usd}",
                "final_usd_value": usd,
            }

        today_rev    = build_metric("Today Revenue",    summary['today_kwh'],    'today')
        month_rev    = build_metric("Month Revenue",    summary['month_kwh'],    'month')
        year_rev     = build_metric("Year Revenue",     summary['year_kwh'],     'year')
        lifetime_rev = build_metric("Lifetime Revenue", summary['lifetime_kwh'], 'lifetime')
 
        return Response({
            "verification_report": {
                "title": "Revenue Calculation Verification Report",
                "unit_id": unit_id,
                "device_id": device_id,
                "generated_at": datetime.now(SITE_TZ).isoformat(),
            },
            "rate_info": {
                "per_unit_cost": per_unit_cost,
                "per_unit_cost_source": (
                    "Device.per_unit_cost is NULL -- defaulted to 0, so all revenue is $0.00"
                    if cost_is_null else
                    "Device.per_unit_cost ($/kWh) from the Device record"
                ),
                "formula": "USD = kWh x Device.per_unit_cost",
                "note": "kWh values match /api/readings/summary/ (year & lifetime include odometer_offset).",
            },
            "metrics": {
                "today_revenue": today_rev,
                "month_revenue": month_rev,
                "year_revenue": year_rev,
                "lifetime_revenue": lifetime_rev,
            },
        })
 
class PeriodEnergyView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        unit_id = (request.query_params.get('unit_id') or '100').strip()
        check_unit_access(request, unit_id)
        from_date = request.query_params.get('from')
        to_date   = request.query_params.get('to')

        if not from_date or not to_date:
            return Response({'error': 'from and to are required'}, status=400)

        try:
            from_dt = datetime.strptime(from_date, '%Y-%m-%d').replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=SITE_TZ
            )
            to_dt = datetime.strptime(to_date, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=SITE_TZ
            )
        except ValueError:
            return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)

        from calendar import monthrange
        qs = SensorReading.objects.filter(unit_id=unit_id, energy_today_kwh__isnull=False)

        if from_dt.day == 1:
            start_val, start_is_override, start_stale = get_month_start_baseline(
                unit_id, from_dt.year, from_dt.month, qs, from_dt
            )
        else:
            start_reading, start_stale, start_age_h = get_baseline_reading(qs, from_dt.timestamp())
            start_val = start_reading.energy_today_kwh if start_reading else None
            start_is_override = False

        last_day_of_to_month = monthrange(to_dt.year, to_dt.month)[1]
        if to_dt.day == last_day_of_to_month:
            end_val, end_is_override, end_stale = get_month_end_baseline(
                unit_id, to_dt.year, to_dt.month, qs, to_dt
            )
        else:
            end_reading, end_stale, end_age_h = get_baseline_reading(qs, to_dt.timestamp())
            end_val = end_reading.energy_today_kwh if end_reading else None
            end_is_override = False

        if end_val is None:
            return Response({
                'period_kwh': None, 'incomplete': True,
                'reason': 'No readings found up to the end of this period.'
            })

        effective_start_stale = start_stale and not start_is_override
        effective_end_stale   = end_stale and not end_is_override

        if effective_start_stale or effective_end_stale:
            return Response({
                'period_kwh': None,
                'incomplete': True,
                'reason': (
                    "Nearest reading to the start/end of this period is "
                    "too far from the requested date (gap in the data). "
                    "Result would be misleading."
                ),
            })

        if start_val is None:
            start_val = end_val

        period_kwh = round(max(0.0, end_val - start_val), 1)
        return Response({'period_kwh': period_kwh, 'incomplete': False})
    
import math

PREDICTION_LOOKBACK_DAYS = 30  # build the "typical day" profile from the last 30 days


def _build_daily_profile(unit_id, ref_now=None, lookback_days=PREDICTION_LOOKBACK_DAYS):
    """
    Average active_power_kw per 5-minute-of-day bucket from this unit's recent
    history. Returns {minute_of_day(step=5) -> avg_kw}. Empty if no history.
    """
    ref_now = ref_now or datetime.now(SITE_TZ)
    start_ts = (ref_now - timedelta(days=lookback_days)).timestamp()
    rows = (SensorReading.objects
            .filter(unit_id=unit_id, timestamp__gte=start_ts,
                    active_power_kw__isnull=False)
            .values_list('timestamp', 'active_power_kw'))
    acc = {}
    for ts, kw in rows.iterator(chunk_size=2000):
        if kw is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=SITE_TZ)
        bucket = ((dt.hour * 60 + dt.minute) // 5) * 5
        a = acc.setdefault(bucket, [0.0, 0])
        a[0] += kw
        a[1] += 1
    return {b: v[0] / v[1] for b, v in acc.items() if v[1] > 0}


def _synthetic_kw(minute_of_day):
    """Gentle daily load shape, used only when a unit has NO history at all."""
    frac = (minute_of_day % 1440) / 1440.0
    return max(0.0, 4.0 + 3.0 * math.sin(2 * math.pi * (frac - 0.25)))


import requests
from django.core.cache import cache

# ── Site location (New York) ──
SITE_LAT, SITE_LON = 40.7128, -74.0060      

# ── Weather → power model (tune these; NO history needed) ──
# Predicted kW = BASE_LOAD + TEMP_COEFF * |forecast_temp − COMFORT_TEMP|
# The |…| makes load rise on BOTH hot days (cooling) and cold days (heating) — a V-shape.
SOLAR_FULL_SUN_WM2    = 1000.0   # irradiance (W/m²) at which a PV array ≈ its peak
PRED_BASE_LOAD_KW  = 4.0    # baseline draw at the comfort temperature
PRED_COMFORT_TEMP_C   = 20.0 # temperature where load is lowest
PRED_TEMP_COEFF_KW = 0.18   # extra kW per °C away from comfort
PRED_MIN_KW        = 0.0
PRED_LOAD_BASE_FRAC   = 0.40     # load at comfort temp = 40% of the device's peak
PRED_LOAD_TEMP_SENS   = 0.020    # +2% of peak per °C away from comfort
PRED_DEFAULT_SOLAR_KW = 100.0
PRED_DEFAULT_LOAD_KW  = 10.0

def _device_category(unit_id):
    """Category used by /api/devices/by-category/ (adjust field name if different)."""
    device = Device.objects.filter(unit_id=str(unit_id).strip()).first()
    if not device:
        return None
    return getattr(device, 'category', None) or getattr(device, 'device_type', None)


def _recent_peak_kw(unit_id, ref_now, days=14):
    """One-query scale anchor: the device's own recent peak kW. None if no history."""
    start_ts = (ref_now - timedelta(days=days)).timestamp()
    peak = (SensorReading.objects
            .filter(unit_id=unit_id, timestamp__gte=start_ts, active_power_kw__isnull=False)
            .aggregate(Max('active_power_kw'))['active_power_kw__max'])
    return abs(peak) if peak else None


def _fetch_weather_forecast(start_dt, end_dt):
    """Hourly temperature + sunlight forecast. Cached 1h. {} maps on failure."""
    cache_key = f"wx_fc_{start_dt.date()}_{end_dt.date()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    now = datetime.now(SITE_TZ)
    past_days = min(92, max(0, (now.date() - start_dt.date()).days))
    fwd_days  = min(16, max(1, (end_dt.date() - now.date()).days + 1))
    params = {
        'latitude': SITE_LAT, 'longitude': SITE_LON,
        'hourly': 'temperature_2m,shortwave_radiation',   # ← sunlight added
        'timezone': 'America/New_York',
        'forecast_days': fwd_days, 'past_days': past_days,
    }
    try:
        resp = requests.get('https://api.open-meteo.com/v1/forecast', params=params, timeout=6)
        resp.raise_for_status()
        h = resp.json().get('hourly', {})
        times = h.get('time', [])
        temps = h.get('temperature_2m', [])
        rads  = h.get('shortwave_radiation', [])
        temp_map, rad_map = {}, {}
        for i, t in enumerate(times):
            dt = datetime.fromisoformat(t).replace(tzinfo=SITE_TZ).replace(minute=0, second=0, microsecond=0)
            if i < len(temps) and temps[i] is not None: temp_map[dt] = temps[i]
            if i < len(rads)  and rads[i]  is not None: rad_map[dt]  = rads[i]
        result = {'temp': temp_map, 'rad': rad_map}
        cache.set(cache_key, result, 3600)
        return result
    except Exception as e:
        logger.error(f"Weather forecast fetch failed: {e}")
        return {'temp': {}, 'rad': {}}
    
def _solar_kw(irradiance_wm2, scale_kw):
    if irradiance_wm2 is None or irradiance_wm2 <= 0:
        return 0.0                                   # night → no generation
    return scale_kw * (irradiance_wm2 / SOLAR_FULL_SUN_WM2)

def _load_kw(temp_c, scale_kw):
    if temp_c is None:
        return scale_kw * PRED_LOAD_BASE_FRAC
    frac = PRED_LOAD_BASE_FRAC + PRED_LOAD_TEMP_SENS * abs(temp_c - PRED_COMFORT_TEMP_C)
    return scale_kw * min(1.0, frac)

def _weather_to_kw(temp_c):
    """Convert a forecasted temperature into an estimated kW. Pure formula — no history."""
    if temp_c is None:
        return PRED_BASE_LOAD_KW
    return max(PRED_MIN_KW,
               PRED_BASE_LOAD_KW + PRED_TEMP_COEFF_KW * abs(temp_c - PRED_COMFORT_TEMP_C))


def _predict_graph_series(unit_id, category, start_dt, end_dt, is_today_view, step_minutes):
    wx = _fetch_weather_forecast(start_dt, end_dt)
    temp_map, rad_map = wx['temp'], wx['rad']
    temp_keys, rad_keys = sorted(temp_map), sorted(rad_map)
    is_solar = (category == 'Solar Generation')

    scale = _recent_peak_kw(unit_id, end_dt) or (PRED_DEFAULT_SOLAR_KW if is_solar else PRED_DEFAULT_LOAD_KW)

    def nearest(d, keys, hk):
        if hk in d: return d[hk]
        if not keys: return None
        return d[min(keys, key=lambda k: abs((k - hk).total_seconds()))]

    labels, values = [], []
    cur, guard = start_dt, 0
    while cur <= end_dt and guard < 20000:
        hk = cur.replace(minute=0, second=0, microsecond=0)
        if is_solar:
            kw = _solar_kw(nearest(rad_map, rad_keys, hk), scale)
        else:
            kw = _load_kw(nearest(temp_map, temp_keys, hk), scale)
        labels.append(cur.strftime('%H:%M') if is_today_view else cur.strftime('%m-%d %H:%M'))
        values.append(round(max(0.0, kw), 3))
        cur += timedelta(minutes=step_minutes)
        guard += 1
    return labels, values

from datetime import datetime, timedelta
# SITE_TZ = ZoneInfo("America/New_York")  ← already defined at top of views.py

class GraphDataView(APIView):
    """
    Consumption-trend graph data (5-minute averaged active power, kW).
    Live behaviour unchanged. When a window has NO readings, an estimated
    ('predicted') series is returned in the SAME shape instead of no_data.
    """
    permission_classes = [IsAuthenticated] 

    def _fallback_response(self, unit_id, category, start_dt, end_dt, is_today_view, effective_period):
        """No real data -> estimated series. Any failure degrades to no_data."""
        try:
            if is_today_view:
                step = 5
            elif effective_period == 'week':
                step = 15
            elif effective_period == 'month':
                step = 60
            else:
                span_days = max(0.0, (end_dt - start_dt).total_seconds() / 86400.0)
                step = 15 if span_days <= 2 else (30 if span_days <= 10 else 60)

            if not category:                              # frontend didn't send it → DB guess
                category = _device_category(unit_id)
            labels, values = _predict_graph_series(unit_id, category, start_dt, end_dt, is_today_view, step)
            if labels:
                return Response({'labels': labels, 'values': values, 'unit': 'kW',
                                 'type': 'predicted', 'is_prediction': True, 'count': len(labels)})
        except Exception as e:
            logger.error(f"Graph prediction fallback failed (unit {unit_id}): {e}")
        return Response({'labels': [], 'values': [], 'unit': 'kW',
                         'type': 'no_data', 'is_prediction': False, 'count': 0})

    def get(self, request):
        unit_id = (request.query_params.get('unit_id') or '100').strip()
        check_unit_access(request, unit_id)  
        category = request.query_params.get('category')        # ← from frontend (authoritative)
        period    = request.query_params.get('period', 'today')
        from_date = request.query_params.get('from')
        to_date   = request.query_params.get('to')

        now      = datetime.now(SITE_TZ)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        is_custom = bool(from_date and to_date)

        if is_custom:
            try:
                start = datetime.fromisoformat(from_date).replace(
                    hour=0, minute=0, second=0, microsecond=0, tzinfo=SITE_TZ)
                end = datetime.fromisoformat(to_date).replace(
                    hour=0, minute=0, second=0, microsecond=0, tzinfo=SITE_TZ) + timedelta(days=1)
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
        elif period == 'today':
            start, end = midnight, now
        elif period == 'week':
            start = midnight - timedelta(days=(now.weekday() + 1) % 7); end = now
        elif period == 'month':
            start = midnight.replace(day=1); end = now
        else:
            start, end = midnight, now

        start_ts, end_ts = start.timestamp(), end.timestamp()
        is_today_view = (period == 'today' and not is_custom)
        effective_period = 'custom' if is_custom else period

        readings = (SensorReading.objects
                    .filter(unit_id=unit_id, timestamp__gte=start_ts, timestamp__lte=end_ts)
                    .order_by('timestamp').only('timestamp', 'active_power_kw'))

        if not readings.exists():
            return self._fallback_response(unit_id, category, start, end, is_today_view, effective_period)

        buckets = {}
        for r in readings.iterator(chunk_size=1000):
            if r.active_power_kw is None:
                continue
            dt = datetime.fromtimestamp(r.timestamp, tz=SITE_TZ)
            key = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)
            b = buckets.setdefault(key, {'total': 0.0, 'count': 0})
            b['total'] += r.active_power_kw; b['count'] += 1

        labels, values = [], []
        for key in sorted(buckets):
            entry = buckets[key]
            if entry['count'] == 0:
                continue
            labels.append(key.strftime('%H:%M') if is_today_view else key.strftime('%m-%d %H:%M'))
            values.append(round(entry['total'] / entry['count'], 3))

        if not labels:
            return self._fallback_response(unit_id, category, start, end, is_today_view, effective_period)

        return Response({'labels': labels, 'values': values, 'unit': 'kW',
                         'type': 'minute_aggregated', 'is_prediction': False, 'count': len(labels)})

class MeterReadingAPIView(APIView):
    """
    POST /meter_readings/  -> store a pre-calculated utility-meter
                                reading directly on SensorReading.
                                No calculation performed here.
    GET  /meter_readings/   -> retrieve those readings, as stored.
    """
 
    def post(self, request):
        try:
            serializer = UtilityMeterReadingSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
 
            reading = serializer.save()
            return Response(
                UtilityMeterReadingSerializer(reading).data,
                status=status.HTTP_201_CREATED
            )
 
        except Exception as e:
            logger.error(f"Error storing utility meter reading: {str(e)}")
            return Response(
                {"error": f"Internal server error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
 
    def get(self, request):
        unit_id = request.query_params.get('unit_id')
        limit = int(request.query_params.get('limit', 10))
 
        queryset = SensorReading.objects.all()
        if unit_id:
            queryset = queryset.filter(unit_id=unit_id)
        queryset = queryset.order_by('-timestamp')[:limit]
 
        serializer = UtilityMeterReadingSerializer(queryset, many=True)
        return Response(serializer.data)
 
 
class RecentReadingsView(APIView):
    """
    Returns readings for the 'Complete Readings' table.
 
    Behavior:
      - No period/from/to given -> legacy behavior (last N readings).
      - period=today/week -> 5-minute intervals (native resolution).
      - period=month -> 30-minute intervals.
      - from+to (custom range) -> 30-minute intervals, capped at 1 month.
 
    Rows are decoded one of two ways, chosen automatically per row:
      - Real block1_raw registers present (cob/100 path) -> decode
        registers exactly as before.
      - block1_raw empty (utility meter path) -> read the stored
        fields directly, no calculation.
    """
    permission_classes = [IsAuthenticated]
    MAX_RANGE_DAYS = 31
 
    @staticmethod
    def _to_signed_16(raw):
        return raw - 65536 if raw > 32767 else raw
 
    def _decode_row_from_registers(self, reading, device=None):
        """UNCHANGED — for rows that have real block1_raw registers."""
        dt = datetime.fromtimestamp(reading.timestamp, tz=EST)
        b = reading.block1_raw or []

        def reg(idx, default=0):
            return b[idx] if len(b) > idx else default

        v_an, v_bn, v_cn, v_avg_ln = reg(0) / 10, reg(1) / 10, reg(2) / 10, reg(3) / 10
        v_ab, v_bc, v_ca, v_avg_ll = reg(4) / 10, reg(5) / 10, reg(6) / 10, reg(7) / 10
        i_a, i_b, i_c = reg(8) / 10, reg(9) / 10, reg(10) / 10
        i_avg, i_demand, i_peak, i_neutral = reg(11) / 10, reg(12) / 10, reg(13) / 10, reg(14) / 10
        freq = reg(15) / 100
        to_signed_16 = self._to_signed_16
        pf_total = round(max(-1.0, min(1.0, to_signed_16(reg(16)) / 10000)), 4)
        pf_a = round(max(-1.0, min(1.0, to_signed_16(reg(17)) / 10000)), 4)
        pf_b = round(max(-1.0, min(1.0, to_signed_16(reg(18)) / 10000)), 4)
        pf_c = round(max(-1.0, min(1.0, to_signed_16(reg(19)) / 10000)), 4)
        kw = round(reading.active_power_kw, 2) if reading.active_power_kw is not None else None
        kvar = round(to_signed_16(reg(21)) / 10, 2)
        kva = round(reg(22) / 100, 2)

        energy = round(reading.energy_today_kwh, 2) if reading.energy_today_kwh else 0

        return {
            'time': dt.strftime('%H:%M:%S'), 'date': dt.strftime('%Y-%m-%d'),
            'kw': kw, 'voltage': round(v_avg_ln, 1), 'current': round(i_avg, 1),
            'energy': energy,
            'pf': pf_total, 'status': 'ok',
            'v_an': round(v_an, 1), 'v_bn': round(v_bn, 1), 'v_cn': round(v_cn, 1),
            'v_avg_ln': round(v_avg_ln, 1),
            'v_ab': round(v_ab, 1), 'v_bc': round(v_bc, 1), 'v_ca': round(v_ca, 1),
            'v_avg_ll': round(v_avg_ll, 1),
            'i_a': round(i_a, 2), 'i_b': round(i_b, 2), 'i_c': round(i_c, 2),
            'i_avg': round(i_avg, 2), 'i_demand': round(i_demand, 2),
            'i_peak': round(i_peak, 2), 'i_neutral': round(i_neutral, 2),
            'frequency': round(freq, 2),
            'pf_a': pf_a, 'pf_b': pf_b, 'pf_c': pf_c,
            'kvar': kvar, 'kva': kva,
        }

    def _decode_row_direct(self, reading, device=None):
        """NEW — utility-meter rows: read stored fields directly, no calculation."""
        dt = datetime.fromtimestamp(reading.timestamp, tz=EST)
        kw = round(reading.active_power_kw, 2) if reading.active_power_kw is not None else None
        energy = round(reading.energy_today_kwh, 2) if reading.energy_today_kwh is not None else None
        return {
            'time': dt.strftime('%H:%M:%S'), 'date': dt.strftime('%Y-%m-%d'),
            'kw': kw, 'voltage': reading.voltage, 'current': reading.current,
            'energy': energy, 'pf': reading.pf_total, 'status': 'ok',
            'v_an': reading.voltage_a_n, 'v_bn': reading.voltage_b_n, 'v_cn': reading.voltage_c_n,
            'v_avg_ln': reading.voltage,
            'v_ab': reading.voltage_a_b, 'v_bc': reading.voltage_b_c, 'v_ca': reading.voltage_c_a,
            'v_avg_ll': reading.voltage_avg_ll,
            'i_a': reading.current_a, 'i_b': reading.current_b, 'i_c': reading.current_c,
            'i_avg': reading.current, 'i_demand': reading.current_demand,
            'i_peak': reading.current_peak_demand, 'i_neutral': reading.current_neutral,
            'frequency': reading.frequency_hz,
            'pf_a': reading.pf_a, 'pf_b': reading.pf_b, 'pf_c': reading.pf_c,
            'kvar': reading.reactive_power_kvar, 'kva': reading.apparent_power_kva,
        }

    def _decode_row(self, reading, device=None):
        if reading.block1_raw and len(reading.block1_raw) >= 23:
            return self._decode_row_from_registers(reading, device)
        return self._decode_row_direct(reading, device)
 
    def get(self, request):
        unit_id = (request.query_params.get('unit_id') or '100').strip()
        check_unit_access(request, unit_id)
        period = (request.query_params.get('period') or '').strip()
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        device = Device.objects.filter(unit_id=unit_id).first()
        now_local = datetime.now(EST)
        start_dt = end_dt = None
        stride_seconds = 300
 
        if from_date and to_date:
            try:
                start_dt = datetime.strptime(from_date, '%Y-%m-%d').replace(
                    hour=0, minute=0, second=0, microsecond=0, tzinfo=EST)
                end_dt = datetime.strptime(to_date, '%Y-%m-%d').replace(
                    hour=23, minute=59, second=59, microsecond=999999, tzinfo=EST)
            except ValueError:
                return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)
 
            range_days = (end_dt.date() - start_dt.date()).days + 1
            if range_days > self.MAX_RANGE_DAYS:
                return Response({
                    'error': f'Date range too large ({range_days} days). '
                             f'Maximum allowed range is {self.MAX_RANGE_DAYS} days (about one month).',
                    'max_range_days': self.MAX_RANGE_DAYS,
                }, status=400)
 
            stride_seconds = 1800
 
        elif period == 'today':
            start_dt = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = now_local
            stride_seconds = 300
 
        elif period == 'week':
            start_dt = (now_local - timedelta(days=(now_local.weekday() + 1) % 7)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            end_dt = now_local
            stride_seconds = 300
 
        elif period == 'month':
            start_dt = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_dt = now_local
            stride_seconds = 1800
 
        base_qs = SensorReading.objects.filter(unit_id=unit_id)
 
        if start_dt and end_dt:
            ranged_qs = base_qs.filter(
                timestamp__gte=start_dt.timestamp(),
                timestamp__lte=end_dt.timestamp()
            ).order_by('timestamp')
 
            picked = []
            last_ts = None
            for r in ranged_qs.iterator(chunk_size=2000):
                if last_ts is None or (r.timestamp - last_ts) >= stride_seconds:
                    picked.append(r)
                    last_ts = r.timestamp
 
            picked.reverse()
            data = [self._decode_row(r, device) for r in picked] 
 
            meta = {
                'mode': 'range',
                'range_from': start_dt.isoformat(),
                'range_to': end_dt.isoformat(),
                'rows_returned': len(data),
                'sampling_interval_seconds': stride_seconds,
                'downsampled': stride_seconds > 300,
            }
        else:
            limit = int(request.query_params.get('limit', 20))
            raw_qs = base_qs.order_by('-timestamp')
            picked = []
            last_ts = None
            GAP_SECONDS = 300
            for r in raw_qs.iterator(chunk_size=500):
                if last_ts is None or (last_ts - r.timestamp) >= GAP_SECONDS:
                    picked.append(r)
                    last_ts = r.timestamp
                    if len(picked) >= limit:
                        break
            data = [self._decode_row(r, device) for r in picked]
            meta = {'mode': 'legacy', 'rows_returned': len(data)}
 
        return Response({'rows': data, 'meta': meta})
    

@admin_required
def customer_list(request):
    """Top-level landing page — list of Customers (e.g. Atlantic County, MPWC)."""
    if request.user.role == 'superadmin':
        customers = Customer.objects.prefetch_related('microgrids').all()
    else:
        ids = request.user.get_accessible_customer_ids()
        customers = Customer.objects.filter(pk__in=ids).prefetch_related('microgrids')
        if not customers.exists():
            messages.error(request, "You don't have any customers assigned yet. Contact a Super Admin.")
    return render(request, 'app1/customer_list.html', {'customers': customers})


@superadmin_required
def add_customer(request):
    form = CustomerForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        customer = form.save(commit=False)
        customer.created_by = request.user
        customer.save()
        messages.success(
            request,
            f'Customer "{customer.name}" created. Next step: click "+ Add Microgrid Site" '
            f'to add the first microgrid for this customer.'
        )
        return redirect('customer_detail', customer_id=customer.pk)
    return render(request, 'app1/add_customer.html', {'form': form})


@superadmin_required
def edit_customer(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Customer "{customer.name}" updated.')
        return redirect('customer_detail', customer_id=customer.pk)
    return render(request, 'app1/edit_customer.html', {'form': form, 'customer': customer})


@superadmin_required
def delete_customer(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    if request.method == 'POST':
        name = customer.name
        customer.delete()   # microgrids under it become customer=NULL (SET_NULL), not deleted
        messages.success(request, f'Customer "{name}" deleted. Its microgrids are now unassigned.')
        return redirect('customer_list')
    return render(request, 'app1/confirm_delete_customer.html', {'customer': customer})


@admin_required
def customer_detail(request, customer_id):
    """Shows all microgrids (sites) belonging to this customer."""
    customer = get_object_or_404(Customer, pk=customer_id)
    if not request.user.can_access_customer(customer_id):
        return HttpResponseForbidden("<h2>403</h2><p>You don't have access to this customer.</p>")

    microgrids = customer.microgrids.prefetch_related('devices').all()
    if request.user.role != 'superadmin':
        allowed = request.user.companies.all()
        microgrids = microgrids.filter(pk__in=allowed)

    return render(request, 'app1/customer_detail.html', {
        'customer': customer,
        'microgrids': microgrids,
    })

# ─────────────────────────────────────────────────────────────────────────────
# COMPANY VIEWS
# ─────────────────────────────────────────────────────────────────────────────

@login_required(login_url='login')
def company_list(request):
    """Microgrid Management — landing page after login for every role.
    Companies (microgrids) are grouped by Customer, and each row carries
    live status / alarm / generation / revenue figures.

    Non-superadmin users with zero assigned microgrids get a dedicated
    polished welcome state instead of an empty data table — no "contact
    a super admin" messaging; 3rd Rock reaches out to them directly
    using the email on file.
    """
    if request.user.role == 'superadmin':
        companies_qs = Company.objects.select_related('customer').prefetch_related('devices').all()
    else:
        companies_qs = request.user.companies.select_related('customer').prefetch_related('devices').all()

    no_access_yet = (request.user.role != 'superadmin') and (not companies_qs.exists())

    companies_qs = companies_qs.order_by('customer__name', 'name')
    companies_list = list(companies_qs)

    total_device_count = 0
    for company in companies_list:
        company.overview = _compute_company_overview(company)
        total_device_count += company.overview['total_devices']

    from collections import OrderedDict
    grouped = OrderedDict()
    unassigned = []
    for company in companies_list:
        if company.customer_id:
            grouped.setdefault(company.customer, []).append(company)
        else:
            unassigned.append(company)

    customer_groups = [{'customer': cust, 'companies': comps} for cust, comps in grouped.items()]
    if unassigned:
        customer_groups.append({'customer': None, 'companies': unassigned})

    return render(request, 'app1/company_list.html', {
        'companies': companies_list,
        'customer_groups': customer_groups,
        'total_device_count': total_device_count,
        'no_access_yet': no_access_yet,
        'hide_chrome': no_access_yet,
    })

@superadmin_required
def add_company(request):
    """Create a new microgrid — optionally pre-filled under a specific customer."""
    initial_customer = None
    customer_id = request.GET.get('customer')
    if customer_id:
        initial_customer = get_object_or_404(Customer, pk=customer_id)

    form = CompanyForm(request.POST or None, initial_customer=initial_customer)
    if request.method == 'POST' and form.is_valid():
        company = form.save(commit=False)
        company.created_by = request.user
        company.save()
        messages.success(
            request,
            f'Microgrid "{company.name}" created. Next step: click "+ Add DER" to add the '
            f'inverters / meters of this microgrid, then add the persons who can see it.'
        )
        return redirect('company_detail', company_id=company.pk)
    customer_contacts = {
        str(c.pk): {'email': c.contact_email, 'phone': c.contact_phone, 'location': c.location}
        for c in Customer.objects.all()
    }
    return render(request, 'app1/add_company.html', {
        'form': form, 'preset_customer': initial_customer,
        'customer_contacts': customer_contacts,
    })

@superadmin_required
def edit_company(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    form = CompanyForm(request.POST or None, instance=company)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Microgrid "{company.name}" updated.')
        if company.customer_id:
            return redirect('customer_detail', customer_id=company.customer_id)
        return redirect('company_detail', company_id=company.pk)
    return render(request, 'app1/edit_company.html', {'form': form, 'company': company})


@superadmin_required
def delete_company(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    customer_id = company.customer_id
    if request.method == 'POST':
        name = company.name
        company.delete()
        messages.success(request, f'Microgrid "{name}" deleted.')
        if customer_id:
            return redirect('customer_detail', customer_id=customer_id)
        return redirect('company_list')
    return render(request, 'app1/confirm_delete_company.html', {'company': company})


@superadmin_required
def company_detail(request, company_id):
    """Superadmin-only device management view for a microgrid."""
    company = get_object_or_404(Company, pk=company_id)
    devices = company.devices.prefetch_related('assigned_users').all()

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

@login_required(login_url='login')
def device_list(request):
    devices = Device.objects.select_related('company').prefetch_related('assigned_users')
    if request.user.role != 'superadmin':
        devices = devices.filter(company_id__in=request.user.get_accessible_company_ids())
    return render(request, 'app1/device_list.html', {'devices': devices})


@superadmin_required
def add_device(request):
    """DERs must always belong to a specific Microgrid — this view now
    requires ?company_id= (passed from that Microgrid's page) and locks
    the DER to it. There is no way to create a DER without a parent."""
    company_id = request.GET.get('company_id')
    if not company_id:
        messages.error(request, 'Please open a specific Microgrid and add the DER from there.')
        return redirect('company_list')

    company = get_object_or_404(Company, pk=company_id)

    form = DeviceForm(request.POST or None, requesting_user=request.user, locked_company=company)
    if request.method == 'POST' and form.is_valid():
        device = form.save(commit=False)
        device.company = company  # enforced server-side regardless of form output
        device.save()
        form.save_m2m()
        DeviceScalingFactorHistory.objects.create(
            device=device, scale_factor=device.scaling_factor,
            effective_from_ts=0, note='Set at DER creation.',
        )
        messages.success(request, f'Device "{device}" created under "{company.name}".')
        return redirect('company_detail', company_id=company.pk)
    return render(request, 'app1/add_device.html', {'form': form, 'company': company})


@superadmin_required
def edit_device(request, device_id):
    device = get_object_or_404(Device, pk=device_id)
    old_scale = device.scaling_factor
    form = DeviceForm(request.POST or None, instance=device, requesting_user=request.user)
    if request.method == 'POST' and form.is_valid():
        updated = form.save()
        if updated.scaling_factor != old_scale:
            DeviceScalingFactorHistory.objects.create(
                device=updated,
                scale_factor=updated.scaling_factor,
                effective_from_ts=timezone.now().timestamp(),
                changed_by=request.user,
            )
            messages.info(
                request,
                f'New scaling factor (×{updated.scaling_factor}) will apply to readings '
                f'received from now on. Existing data is untouched by this save — use '
                f'the rebuild command if you need to backdate a correction.'
            )
        messages.success(request, f'Device "{updated}" updated.')
        return redirect('device_detail', device_id=updated.pk)
    return render(request, 'app1/edit_device.html', {'form': form, 'device': device})


@superadmin_required
def delete_device(request, device_id):
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
    device = get_object_or_404(Device, pk=device_id)
    if not request.user.can_access_unit(device.unit_id):
        return HttpResponseForbidden("<h2>403</h2><p>You don't have access to this device.</p>")
    try:
        latest = SensorReading.objects.filter(unit_id=device.unit_id).latest('timestamp')
    except SensorReading.DoesNotExist:
        latest = None

    return render(request, 'app1/device_detail.html', {
        'device': device, 'latest': latest,
    })
    
from io import BytesIO
from django.http import FileResponse, HttpResponseForbidden
from django.urls import reverse

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

VENDOR_CONTACT_NAME = "Vijay Israni"
VENDOR_CONTACT_PHONE = "(650) 922 1970"
VENDOR_REMIT_ADDRESS = "37 Northcrest Drive, So San Francisco, CA 94080"


def _reading_at_or_before(unit_id, anchor_dt):
    """Same pattern as PeriodEnergyView / _baseline_kwh."""
    anchor_ts = anchor_dt.timestamp()
    qs = SensorReading.objects.filter(unit_id=unit_id, energy_today_kwh__isnull=False)
    r = qs.filter(timestamp__lte=anchor_ts).order_by('-timestamp').first()
    if r:
        return r.energy_today_kwh
    r2 = qs.order_by('timestamp').first()
    return r2.energy_today_kwh if r2 else None

def _next_invoice_reference(as_of_date):
    """
    Generates MMDDYYYY-NN, incrementing NN for each invoice generated
    on the same calendar date. select_for_update() + atomic() prevents
    two concurrent bill generations from getting the same serial.
    """
    date_key = as_of_date.strftime('%m%d%Y')
    with transaction.atomic():
        seq, _ = InvoiceSequence.objects.select_for_update().get_or_create(
            date_key=date_key, defaults={'last_serial': 0}
        )
        seq.last_serial += 1
        seq.save(update_fields=['last_serial'])
        serial = seq.last_serial
    return f"{date_key}-{serial:02d}"

def _check_device_access(request, device):
    """
    Bill generation access: Super Admin always allowed. Everyone else must
    have this device's unit_id reachable through one of their assigned
    Microgrids (companies) — matches the same rule used everywhere else
    (dashboard, graphs, readings). Legacy per-device assigned_users is
    checked too, for any device that still uses that older field.
    """
    if request.user.role == 'superadmin':
        return True
    if request.user.can_access_unit(device.unit_id):
        return True
    if device.assigned_users.filter(pk=request.user.pk).exists():
        return True
    return False


import calendar

@login_required(login_url='login')
def select_bill_month(request, device_id):
    device = get_object_or_404(Device, pk=device_id)
    if not _check_device_access(request, device):
        return HttpResponseForbidden("<h2>403</h2><p>You don't have access to this device.</p>")

    now = datetime.now(SITE_TZ)
    months = []
    y, m = now.year, now.month

    # Step back to the last COMPLETED month before building the list.
    # e.g. if today is in July, the most recent billable month is June.
    m -= 1
    if m == 0:
        m = 12
        y -= 1

    for _ in range(24):
        months.append({
            'value': f"{y}-{m:02d}",
            'label': f"{calendar.month_name[m]} {y}",
        })
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    return render(request, 'app1/select_bill_month.html', {
        'device': device, 'months': months, 'error': request.GET.get('error'),
    })

from reportlab.platypus import KeepTogether

from django.template.loader import render_to_string
from xhtml2pdf import pisa
from django.contrib.staticfiles import finders

def format_kwh(value):
    """US-format a kWh value for display: round to nearest whole number, comma-separated."""
    try:
        return f"{round(float(value)):,}"
    except (TypeError, ValueError):
        return "0"

@login_required(login_url='login')
def generate_bill(request, device_id):
    device = get_object_or_404(Device, pk=device_id)
    if not _check_device_access(request, device):
        return HttpResponseForbidden("<h2>403</h2><p>You don't have access to this device.</p>")

    try:
        year, month = (int(x) for x in request.GET.get('period', '').split('-'))
    except ValueError:
        return redirect(reverse('select_bill_month', args=[device_id]) + '?error=Please choose a valid month.')

    month_start = datetime(year, month, 1, tzinfo=SITE_TZ)
    next_month_start = (datetime(year + 1, 1, 1, tzinfo=SITE_TZ) if month == 12
                         else datetime(year, month + 1, 1, tzinfo=SITE_TZ))

    qs_for_bill = SensorReading.objects.filter(unit_id=device.unit_id, energy_today_kwh__isnull=False)
    beginning, _, _ = get_month_start_baseline(device.unit_id, year, month, qs_for_bill, month_start)
    end, _, _ = get_month_end_baseline(device.unit_id, year, month, qs_for_bill, next_month_start)

    if beginning is None:
        beginning = _reading_at_or_before(device.unit_id, month_start)
    if end is None:
        end = _reading_at_or_before(device.unit_id, next_month_start)

    if beginning is None or end is None:
        return redirect(reverse('select_bill_month', args=[device_id]) + '?error=No meter data for that period yet.')

    # ── Apply odometer offset so the bill shows GATS-aligned cumulative readings ──
    offset = float(device.odometer_offset or 0)
    beginning = beginning + offset
    end = end + offset

    net_kwh = max(0.0, end - beginning)
    as_of = next_month_start.date()

    cost_bd = get_escalated_cost_breakdown(device, as_of)
    rate = cost_bd['escalated_cost']
    charges = calculate_revenue(device, net_kwh, as_of, metric='month')
    tax = 0.00
    total_due = round(charges + tax, 2)

    billing_days = (next_month_start - month_start).days
    billing_date = next_month_start.date()
    core_billing_ref = _next_invoice_reference(datetime.now(SITE_TZ).date())
    prefix = (device.billing_prefix or '').strip().upper()
    billing_ref = f"{prefix} {core_billing_ref}" if prefix else core_billing_ref

    billing_period_label = month_start.strftime('%B %Y')
    escalator_pct = device.escalator if device.escalator is not None else 0

    company = device.company
    company_name = company.name if company else ''
    company_address = company.location if company else ''
    project_location = device.location or ''

    # Resolve logo as an absolute filesystem path (needed by xhtml2pdf's
    # link_callback below to embed the image in the PDF)
    bill_logo = BillLogo.get_current()
    logo_path = bill_logo.logo.path if bill_logo and bill_logo.logo else None

    context = {
        'billing_date_str': billing_date.strftime('%B %d, %Y'),
        'billing_date_str_full': month_start.strftime('%B %d, %Y'),
        'billing_ref': billing_ref,
        'billing_period_label': billing_period_label,
        'billing_period_compact': f"{month_start.strftime('%B')}{year}",
        'project_location': project_location,
        'company_name': company_name,
        'company_address': company_address,
        'logo_url': logo_path,  # absolute path; resolved by link_callback
        'vendor_contact_name': VENDOR_CONTACT_NAME,
        'vendor_contact_phone': VENDOR_CONTACT_PHONE,
        'vendor_remit_address': VENDOR_REMIT_ADDRESS,
        'service_from': month_start.strftime('%d %b %Y'),
        'service_to': (next_month_start - timedelta(days=1)).strftime('%d %b %Y'),
        'billing_days': billing_days,
        'beginning_fmt': format_kwh(beginning),
        'end_fmt': format_kwh(end),
        'net_kwh_fmt': format_kwh(net_kwh),
        'rate_fmt': f"{rate:.3f}",
        'escalator_pct_fmt': f"{escalator_pct:.3f}",
        'charges_fmt': f"{charges:,.2f}",
        'charges_fmt2': f"{charges:,.2f}",
        'tax_fmt': f"{tax:,.4f}",
        'total_due_fmt': f"{total_due:,.2f}",
        'routing_number': device.billing_routing_number or '',
        'account_number': device.billing_account_number or '',
    }

    html_string = render_to_string('app1/bill_pdf.html', context)

    def link_callback(uri, rel):
        # xhtml2pdf needs local filesystem paths for images/CSS, not URLs.
        # Since we already pass logo_url as an absolute path in context,
        # this just returns it unchanged if it's already a valid local file.
        if os.path.isfile(uri):
            return uri
        result = finders.find(uri.replace(settings.STATIC_URL, '') if hasattr(settings, 'STATIC_URL') else uri)
        return result or uri

    buf = BytesIO()
    pisa_status = pisa.CreatePDF(html_string, dest=buf, link_callback=link_callback)

    if pisa_status.err:
        return HttpResponse('PDF generation failed. Check server logs.', status=500)

    buf.seek(0)
    filename = f"Bill_{device.unit_id}_{month_start.strftime('%b_%Y')}.pdf"
    return FileResponse(buf, as_attachment=True, filename=filename, content_type='application/pdf')

@admin_required
def upload_bill_logo(request):
    """Admin uploads/replaces the invoice logo shown on generated bills."""
    current = BillLogo.get_current()
    form = BillLogoForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Invoice logo updated.')
        return redirect('upload_bill_logo')
    return render(request, 'app1/upload_bill_logo.html', {'form': form, 'current': current})

class DevicesByCategoryView(APIView):
    """
    Returns all active devices grouped by device_category.
    Optional ?company_id=<id> scopes results to a single microgrid
    (used by the Home page once a microgrid has been selected).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        queryset = Device.objects.filter(is_active=True).select_related('company')

        company_id = request.query_params.get('company_id')
        if company_id:
            if not user.can_access_company(company_id):
                raise PermissionDenied("You do not have access to this microgrid.")
            queryset = queryset.filter(company_id=company_id)

        if user.role != 'superadmin':
            unit_ids = user.get_accessible_unit_ids()
            if unit_ids:
                queryset = queryset.filter(unit_id__in=unit_ids)
            else:
                return Response({})

        ALL_CATEGORIES = [
            'Meters', 'Solar Generation', 'Battery Power',
            'EV Charging', 'Building Load', 'HVAC', 'Genset',
        ]
        result = {cat: [] for cat in ALL_CATEGORIES}

        for device in queryset.order_by('device_category', 'unit_id'):
            cat = device.device_category
            if cat not in result:
                result[cat] = []
            result[cat].append({
                'unit_id': device.unit_id,
                'display_name': device.get_display_name(),
                'location': device.location or '',
                'company': device.company.name if device.company else '',
                'per_unit_cost': str(device.per_unit_cost) if device.per_unit_cost else None,
            })

        return Response(result)
    
LAST_COMPANY_SESSION_KEY = 'last_company_id'
    
@login_required(login_url='login')
def energy_dashboard_v2(request, company_id=None):
    """DER detail/graph dashboard. When reached from a specific microgrid's
    Home page, company_id scopes the navbar and devices to ONLY that
    microgrid — this is what was missing before, causing "All Meters" /
    "All Solar Generation" to pull in every microgrid's devices."""
    if company_id is None:
        last_id = request.session.get(LAST_COMPANY_SESSION_KEY)
        if last_id and request.user.can_access_company(last_id):
            return redirect('energy_dashboard_v2_company', company_id=last_id)

        messages.info(request, "Please select a microgrid first.")
        return redirect('company_list')

    company = get_object_or_404(Company, pk=company_id)
    if not request.user.can_access_company(company_id):
        return HttpResponseForbidden(
            "<h2>403</h2><p>You don't have access to this microgrid.</p>"
        )

    request.session[LAST_COMPANY_SESSION_KEY] = company_id

    if request.user.role == 'superadmin':
        unit_ids = list(Device.objects.filter(is_active=True).values_list('unit_id', flat=True))
    else:
        unit_ids = request.user.get_accessible_unit_ids()
    if not unit_ids:
        unit_ids = ['100']

    primary_unit = unit_ids[0]
    context = {
        'user': request.user,
        'unit_ids': unit_ids,
        'primary_unit': primary_unit,
        'company': company,
    }
    return render(request, 'app1/energy_dashboard_v2.html', context)

@login_required(login_url='login')
def home(request, company_id=None):
    """
    'Home' with no company_id is the top-level NOC/overview entry point —
    per Vijay's explicit request, it must ALWAYS land on the full
    Microgrid Management list, never silently jump back into whichever
    microgrid was last viewed. The "remember last company" behavior is
    intentionally removed here.
    """
    if company_id is None:
        return redirect('company_list')

    company = get_object_or_404(Company, pk=company_id)
    if not request.user.can_access_company(company_id):
        return HttpResponseForbidden("<h2>403</h2><p>You don't have access to this microgrid.</p>")

    request.session[LAST_COMPANY_SESSION_KEY] = company_id
    return render(request, 'app1/home.html', {'company': company})



def _baseline_kwh(qs, anchor_ts, fallback):
    r = qs.filter(timestamp__lte=anchor_ts).order_by('-timestamp').first()
    if r is not None:
        return r.energy_today_kwh
    r = qs.filter(timestamp__gt=anchor_ts).order_by('timestamp').first()  # earliest available
    return r.energy_today_kwh if r is not None else fallback

class PeriodRevenueView(APIView):
    permission_classes = [IsAuthenticated] 
    def get(self, request):
        unit_id = (request.query_params.get('unit_id') or '100').strip()
        check_unit_access(request, unit_id)   
        from_date = request.query_params.get('from')
        to_date   = request.query_params.get('to')
        if not from_date or not to_date:
            return Response({'error': 'from and to are required'}, status=400)
        try:
            from_dt = datetime.strptime(from_date, '%Y-%m-%d').replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=SITE_TZ)
            to_dt = datetime.strptime(to_date, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=SITE_TZ)
        except ValueError:
            return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)

        from calendar import monthrange
        device = Device.objects.filter(unit_id=unit_id).first()
        qs = SensorReading.objects.filter(unit_id=unit_id, energy_today_kwh__isnull=False)

        if from_dt.day == 1:
            start_val, start_is_override, start_stale = get_month_start_baseline(
                unit_id, from_dt.year, from_dt.month, qs, from_dt
            )
        else:
            start_reading, start_stale, start_age_h = get_baseline_reading(qs, from_dt.timestamp())
            start_val = start_reading.energy_today_kwh if start_reading else None
            start_is_override = False

        last_day_of_to_month = monthrange(to_dt.year, to_dt.month)[1]
        if to_dt.day == last_day_of_to_month:
            end_val, end_is_override, end_stale = get_month_end_baseline(
                unit_id, to_dt.year, to_dt.month, qs, to_dt
            )
        else:
            end_reading, end_stale, end_age_h = get_baseline_reading(qs, to_dt.timestamp())
            end_val = end_reading.energy_today_kwh if end_reading else None
            end_is_override = False

        effective_start_stale = start_stale and not start_is_override
        effective_end_stale   = end_stale and not end_is_override

        if end_val is None or effective_start_stale or effective_end_stale:
            return Response({
                'period_kwh': None, 'period_usd': None, 'incomplete': True,
                'reason': 'Data gap near the start or end of this period.',
            })

        if start_val is None:
            start_val = end_val

        period_kwh = round(max(0.0, end_val - start_val), 1)

        cost_bd = get_escalated_cost_breakdown(device, to_dt.date())
        period_usd = calculate_revenue(device, period_kwh, to_dt.date(), metric='custom-range')

        return Response({
            'period_kwh': period_kwh,
            'rate_applied': cost_bd['escalated_cost'],
            'base_cost': cost_bd['base_cost'],
            'escalated_cost': cost_bd['escalated_cost'],
            'escalator_pct': cost_bd['escalator_pct'],
            'effective_date': cost_bd['effective_date'].isoformat() if cost_bd['effective_date'] else None,
            'years_elapsed': cost_bd['years_elapsed'],
            'as_of_date': to_date,
            'period_usd': period_usd,
            'incomplete': False,
        })
        
        
class GatsMeterReadingView(APIView):
    """
    GET /gats_readings/?unit_id=100
        -> unchanged: latest raw reading + GATS offset (original behavior)

    GET /gats_readings/?unit_id=100&month=2026-07
        -> NEW: returns Previous/Current/Net readings for that month,
           ready to drop directly into a RequestLoadGen submission.

    Beginning reading is chained from the last sensor reading AT/BEFORE
    the month starts (not the month's own first reading) — this avoids
    the June-style mismatch we found earlier where a month's own
    "beginning" value didn't line up with the prior month's true end.
    """
    permission_classes = [IsAuthenticated]  
    
    def get(self, request):
        unit_id = (request.query_params.get('unit_id') or '100').strip()
        check_unit_access(request, unit_id) 
        month_param = request.query_params.get('month')  # expects YYYY-MM

        device = Device.objects.filter(unit_id=unit_id).first()
        if device is None:
            return Response({"error": f"No device found for unit_id={unit_id}"}, status=404)

        gats_offset = float(device.gats_offset or 0)
        qs = SensorReading.objects.filter(unit_id=unit_id, energy_today_kwh__isnull=False)

        if month_param:
            try:
                year, month = map(int, month_param.split('-'))
            except (ValueError, IndexError):
                return Response({"error": "Invalid month format. Use YYYY-MM, e.g. 2026-07."}, status=400)

            month_start = datetime(year, month, 1, tzinfo=SITE_TZ)
            next_month_start = (datetime(year + 1, 1, 1, tzinfo=SITE_TZ) if month == 12
                                else datetime(year, month + 1, 1, tzinfo=SITE_TZ))

            raw_begin, begin_is_override, begin_stale = get_month_start_baseline(
                unit_id, year, month, qs, month_start
            )
            raw_end, end_is_override, end_stale = get_month_end_baseline(
                unit_id, year, month, qs, next_month_start
            )

            if raw_begin is None or raw_end is None:
                return Response({"error": "No sensor data available for this month."}, status=404)

            gats_begin = round(raw_begin + gats_offset, 3)
            gats_end = round(raw_end + gats_offset, 3)
            net_generation = round(gats_end - gats_begin, 3)

            return Response({
                "unit_id": unit_id,
                "month": f"{year:04d}-{month:02d}",
                "gats_offset": gats_offset,
                "ready_for_requestloadgen": {
                    "PreviousMeterReading": gats_begin,
                    "CurrentMeterReading": gats_end,
                    "NetGeneration": net_generation,
                    "MonthYearGeneration": f"{month:02d}{year:04d}",
                },
                "beginning_reading_source": {
                    "raw_value": raw_begin,
                    "used_override": begin_is_override,
                    "stale": begin_stale,
                },
                "ending_reading_source": {
                    "raw_value": raw_end,
                    "used_override": end_is_override,
                    "stale": end_stale,
                },
            })

        # ── No month given: original behavior, unchanged ──
        latest = qs.order_by('-timestamp').first()
        if latest is None:
            return Response({"error": "No readings found for this unit"}, status=404)

        raw_reading = latest.energy_today_kwh
        gats_aligned_reading = raw_reading + gats_offset

        return Response({
            "unit_id": unit_id,
            "raw_meter_reading": raw_reading,
            "gats_offset": gats_offset,
            "gats_offset_datetime": device.gats_offset_datetime,
            "gats_aligned_current_reading": round(gats_aligned_reading, 3),
            "reading_timestamp": latest.timestamp,
            "reading_local_est": datetime.fromtimestamp(latest.timestamp, tz=SITE_TZ).isoformat(),
        })