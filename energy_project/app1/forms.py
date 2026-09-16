from django import forms
from django.contrib.auth.forms import AuthenticationForm
from .models import User
from .models import *
from django.contrib.auth import authenticate


class LoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'placeholder': 'Email address',
            'autocomplete': 'email',
            'class': 'auth-input',
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Password',
            'autocomplete': 'current-password',
            'class': 'auth-input',
        })
    )

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        super().__init__(*args, **kwargs)

    def clean(self):
        email = self.cleaned_data.get('email')
        password = self.cleaned_data.get('password')
        if email and password:
            self.user_cache = authenticate(self.request, username=email, password=password)
            if self.user_cache is None:
                raise forms.ValidationError("Please enter a correct email and password.")
            elif not self.user_cache.is_active:
                raise forms.ValidationError("This account is inactive.")
        return self.cleaned_data

    def get_user(self):
        return self.user_cache


from django.contrib.auth.forms import UsernameField
from django.core.exceptions import ValidationError
import uuid


class SignUpForm(forms.ModelForm):
    """
    Email-only signup, now with an optional phone number. No username
    field shown, no password collected — OTP is the only login path.
    User must supply at least one of email/phone to proceed.
    """
    first_name = forms.CharField(
        max_length=150, required=True,
        widget=forms.TextInput(attrs={'class': 'auth-input', 'placeholder': 'First name'})
    )
    last_name = forms.CharField(
        max_length=150, required=True,
        widget=forms.TextInput(attrs={'class': 'auth-input', 'placeholder': 'Last name'})
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={'class': 'auth-input', 'placeholder': 'you@company.com'})
    )
    phone = forms.CharField(
        max_length=20, required=False,
        widget=forms.TextInput(attrs={'class': 'auth-input', 'placeholder': 'Phone number'})
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone']

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise ValidationError('An account with this email already exists.')
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        if phone and User.objects.filter(phone=phone).exists():
            raise ValidationError('An account with this phone number already exists.')
        return phone

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email')
        phone = cleaned_data.get('phone')
        if not email and not phone:
            raise ValidationError('Please provide an email address or phone number.')
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        email = self.cleaned_data.get('email')
        phone = self.cleaned_data.get('phone')

        if email:
            user.email = email
            base = email.split('@')[0]
        else:
            user.email = ''
            base = phone.replace('+', '').replace(' ', '') or 'user'

        # Username is required internally by AbstractUser but never shown
        # or used for login — generate a unique, opaque value.
        user.username = f"{base}-{uuid.uuid4().hex[:8]}"
        user.set_unusable_password()   # OTP-only login — no password exists
        if commit:
            user.save()
        return user


class AddUserForm(forms.ModelForm):
    """Admin form for adding users with role assignment.
    No username or password collected — OTP is the only login path now,
    so a password serves no purpose, and email is the sole identifier."""

    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name','phone', 'role', 'companies']
        widgets = {
            'email': forms.EmailInput(attrs={'placeholder': 'Email address', 'class': 'auth-input'}),
            'first_name': forms.TextInput(attrs={'placeholder': 'First name', 'class': 'auth-input'}),
            'last_name': forms.TextInput(attrs={'placeholder': 'Last name', 'class': 'auth-input'}),
            'phone': forms.TextInput(attrs={'placeholder': 'Phone number', 'class': 'auth-input'}),
            'role': forms.Select(attrs={'class': 'auth-input'}),
            'companies': forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, requesting_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['companies'].required = False
        self.fields['companies'].label = 'Microgrids'

        if requesting_user and requesting_user.role != 'superadmin':
            self.fields['companies'].queryset = requesting_user.companies.all()
            self.fields['role'].choices = [
                c for c in User.ROLE_CHOICES if c[0] in ('user', 'operator')
            ]
        else:
            self.fields['companies'].queryset = Company.objects.all().order_by('name')

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        # Username is required internally by AbstractUser but never shown
        # or used for login — generate a unique, opaque value from the email.
        user.username = f"{user.email.split('@')[0]}-{uuid.uuid4().hex[:8]}"
        user.set_unusable_password()  # OTP-only login — no password exists
        if commit:
            user.save()
            self.save_m2m()
        return user


class EditUserForm(forms.ModelForm):
    """Admin/Super Admin form for editing an existing user's info, role, and companies."""

    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name','phone', 'role', 'companies']
        labels = {
            'companies': 'Microgrids',
        }
        widgets = {
            'email': forms.EmailInput(attrs={'placeholder': 'Email address', 'class': 'auth-input'}),
            'first_name': forms.TextInput(attrs={'placeholder': 'First name', 'class': 'auth-input'}),
            'last_name': forms.TextInput(attrs={'placeholder': 'Last name', 'class': 'auth-input'}),
            'phone': forms.TextInput(attrs={'placeholder': 'Phone number', 'class': 'auth-input'}),
            'role': forms.Select(attrs={'class': 'auth-input'}),
            'companies': forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, requesting_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['companies'].required = False

        if requesting_user and requesting_user.role != 'superadmin':
            self.fields['companies'].queryset = requesting_user.companies.all()
            self.fields['role'].choices = [
                c for c in User.ROLE_CHOICES if c[0] in ('user', 'operator')
            ]
        else:
            self.fields['companies'].queryset = Company.objects.all().order_by('name')
            
from django import forms
from .models import Company, Device, User, DEVICE_CATEGORY_CHOICES


class AssignUnitsForm(forms.Form):
    """Form for assigning unit IDs to a user"""
    unit_ids_input = forms.CharField(
        label='Unit IDs',
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. 100, 101, 102',
            'class': 'auth-input',
        }),
        help_text='Comma-separated unit IDs'
    )

    def clean_unit_ids_input(self):
        raw = self.cleaned_data.get('unit_ids_input', '')
        if not raw.strip():
            return []
        return [x.strip() for x in raw.split(',') if x.strip()]

class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['name', 'location', 'description', 'contact_email', 'contact_phone']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'fi', 'placeholder': 'e.g. Atlantic County'}),
            'location': forms.TextInput(attrs={'class': 'fi', 'placeholder': 'e.g. Atlantic City, NJ'}),
            'description': forms.Textarea(attrs={'class': 'fi', 'rows': 3, 'placeholder': 'Optional description…'}),
            'contact_email': forms.EmailInput(attrs={'class': 'fi', 'placeholder': 'contact@customer.com'}),
            'contact_phone': forms.TextInput(attrs={'class': 'fi', 'placeholder': '+1 555 000 0000'}),
        }
        
        
class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ['name', 'location', 'description', 'contact_email', 'contact_phone', 'customer']
        labels = {
            'name': 'Microgrid Name',
            'customer': 'Customer',
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'fi', 'placeholder': 'e.g. Acme Industries'}),
            'location': forms.TextInput(attrs={'class': 'fi', 'placeholder': 'e.g. Mumbai, Maharashtra'}),
            'description': forms.Textarea(attrs={'class': 'fi', 'rows': 3, 'placeholder': 'Optional description…'}),
            'contact_email': forms.EmailInput(attrs={'class': 'fi', 'placeholder': 'contact@company.com'}),
            'contact_phone': forms.TextInput(attrs={'class': 'fi', 'placeholder': '+91 98000 00000'}),
            'customer': forms.Select(attrs={'class': 'fi'}),
        }

    def __init__(self, *args, initial_customer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].required = False
        self.fields['customer'].empty_label = '— No Customer (Unassigned) —'
        self.fields['customer'].queryset = Customer.objects.all().order_by('name')
        if initial_customer and not self.instance.pk:
            self.initial['customer'] = initial_customer.pk

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            return name
        qs = Company.objects.filter(name__iexact=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        existing = qs.first()
        if existing:
            owner = f" (under customer: {existing.customer.name})" if existing.customer else " (no customer assigned)"
            raise forms.ValidationError(
                f'A microgrid named "{existing.name}" already exists{owner}. '
                f'Please use a different name, or open that existing microgrid instead of creating a new one.'
            )
        return name


class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = [
            'unit_id', 'device_name', 'device_category', 'per_unit_cost', 'escalator', 'effective_date',
            'odometer_offset', 'odometer_offset_datetime', 'gats_offset', 'gats_offset_datetime','scaling_factor',
            'billing_prefix', 'description', 'location', 'company', 'assigned_users', 'is_active',
        ]
        labels = {
            'company': 'Microgrid',
        }
        widgets = {
            'unit_id': forms.TextInput(attrs={'class': 'fi', 'placeholder': 'e.g. 100, ABC101, Unit100'}),
            'device_name': forms.TextInput(attrs={'class': 'fi', 'placeholder': 'e.g. Main Panel, Rooftop Solar Array'}),
            'device_category': forms.Select(attrs={'class': 'fi'}),
            'per_unit_cost': forms.NumberInput(attrs={'class': 'fi', 'placeholder': 'e.g. 0.14', 'step': '0.001'}),
            'escalator': forms.NumberInput(attrs={'class': 'fi', 'placeholder': 'e.g. 2.5', 'step': '0.001'}),
            'effective_date': forms.DateInput(attrs={'class': 'fi', 'type': 'date'}, format='%Y-%m-%d'),
            'odometer_offset': forms.NumberInput(attrs={'class': 'fi', 'step': '0.001'}),
            'odometer_offset_datetime': forms.DateTimeInput(
                attrs={'class': 'fi', 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'gats_offset': forms.NumberInput(attrs={'class': 'fi', 'step': '0.001'}),
            'gats_offset_datetime': forms.DateTimeInput(
                attrs={'class': 'fi', 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'scaling_factor': forms.NumberInput(attrs={
                'class': 'fi', 'placeholder': 'e.g. 0.5', 'step': '0.0001'
            }),
            'billing_prefix': forms.TextInput(attrs={
            'class': 'fi',
            'placeholder': 'e.g. CGREF',
            'style': 'text-transform:uppercase;',
            'maxlength': '6',
                }),
            'description': forms.Textarea(attrs={'class': 'fi', 'rows': 3}),
            'location': forms.TextInput(attrs={'class': 'fi', 'placeholder': 'e.g. Building A, Floor 2'}),
            'company': forms.Select(attrs={'class': 'fi'}),
            'assigned_users': forms.SelectMultiple(attrs={'class': 'fi', 'size': 6}),
            'is_active': forms.CheckboxInput(attrs={'class': 'fi-check'}),
        }

    def __init__(self, *args, requesting_user=None, locked_company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_users'].required = False
        self.fields['per_unit_cost'].required = False
        self.fields['effective_date'].input_formats = ['%Y-%m-%d']
        self.fields['odometer_offset_datetime'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['gats_offset_datetime'].input_formats = ['%Y-%m-%dT%H:%M']

        # Every DER must have a parent Microgrid — no "unassigned" option.
        self.fields['company'].required = True
        self.fields['company'].empty_label = None

        if locked_company is not None:
            # Reached via a specific Microgrid's "+ Add DER" — lock it in.
            self.fields['company'].queryset = Company.objects.filter(pk=locked_company.pk)
            self.fields['company'].initial = locked_company.pk
            self.fields['company'].disabled = True

        if requesting_user and requesting_user.role != 'superadmin':
            if locked_company is None:
                self.fields['company'].queryset = requesting_user.companies.all()
            self.fields['assigned_users'].queryset = User.objects.filter(
                companies__in=requesting_user.companies.all()
            ).distinct().order_by('username')
        else:
            self.fields['assigned_users'].queryset = User.objects.all().order_by('username')

        def _trim(value):
            s = format(value, 'f')
            if '.' in s:
                s = s.rstrip('0').rstrip('.')
            return s

        if self.instance and self.instance.pk:
            if self.instance.per_unit_cost is not None:
                self.initial['per_unit_cost'] = _trim(self.instance.per_unit_cost)
            if self.instance.escalator is not None:
                self.initial['escalator'] = _trim(self.instance.escalator)
            if self.instance.odometer_offset is not None:
                self.initial['odometer_offset'] = _trim(self.instance.odometer_offset)
            if self.instance.gats_offset is not None:
                self.initial['gats_offset'] = _trim(self.instance.gats_offset)
            if self.instance.scaling_factor is not None:
                self.initial['scaling_factor'] = _trim(self.instance.scaling_factor)

    def clean_unit_id(self):
        return (self.cleaned_data.get('unit_id') or '').strip()
    
    def clean_billing_prefix(self):
        value = self.cleaned_data.get('billing_prefix', '')
        return value.strip().upper() if value else value
    
    def clean_scaling_factor(self):
        value = self.cleaned_data.get('scaling_factor')
        if value is not None and value <= 0:
            raise forms.ValidationError("Scaling factor must be greater than zero.")
        return value

class AssignDeviceToCompanyForm(forms.Form):
    """Quick form to move a device to a company from the company detail page."""
    unit_id = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'fi', 'placeholder': 'Unit ID'}),
        label='Unit ID'
    )
    device_category = forms.ChoiceField(
        choices=DEVICE_CATEGORY_CHOICES,
        widget=forms.Select(attrs={'class': 'fi'}),
        label='Device Category'
    )
    
    
class BillLogoForm(forms.ModelForm):
    class Meta:
        model = BillLogo
        fields = ['logo']
        widgets = {
            'logo': forms.ClearableFileInput(attrs={'class': 'fi', 'accept': 'image/*'}),
        }