from django import forms
from django.contrib.auth.forms import AuthenticationForm
from .models import User


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={
            'placeholder': 'Username',
            'autocomplete': 'username',
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


class SignUpForm(forms.ModelForm):
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Create password',
            'class': 'auth-input',
        })
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Confirm password',
            'class': 'auth-input',
        })
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name']
        widgets = {
            'username': forms.TextInput(attrs={'placeholder': 'Username', 'class': 'auth-input'}),
            'email': forms.EmailInput(attrs={'placeholder': 'Email address', 'class': 'auth-input'}),
            'first_name': forms.TextInput(attrs={'placeholder': 'First name', 'class': 'auth-input'}),
            'last_name': forms.TextInput(attrs={'placeholder': 'Last name', 'class': 'auth-input'}),
        }

    def clean_password2(self):
        p1 = self.cleaned_data.get('password1')
        p2 = self.cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords don't match.")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        user.role = 'user'
        if commit:
            user.save()
        return user


class AddUserForm(forms.ModelForm):
    """Admin form for adding users with role assignment"""
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Set password',
            'class': 'auth-input',
        })
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Confirm password',
            'class': 'auth-input',
        })
    )
    unit_ids_input = forms.CharField(
        label='Unit IDs',
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. 100, 101, 102',
            'class': 'auth-input',
        }),
        help_text='Comma-separated unit IDs'
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'role']
        widgets = {
            'username': forms.TextInput(attrs={'placeholder': 'Username', 'class': 'auth-input'}),
            'email': forms.EmailInput(attrs={'placeholder': 'Email address', 'class': 'auth-input'}),
            'first_name': forms.TextInput(attrs={'placeholder': 'First name', 'class': 'auth-input'}),
            'last_name': forms.TextInput(attrs={'placeholder': 'Last name', 'class': 'auth-input'}),
            'role': forms.Select(attrs={'class': 'auth-input'}),
        }

    def clean_password2(self):
        p1 = self.cleaned_data.get('password1')
        p2 = self.cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords don't match.")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        # Parse unit_ids from comma-separated input
        raw = self.cleaned_data.get('unit_ids_input', '')
        if raw.strip():
            try:
                user.unit_ids = [int(x.strip()) for x in raw.split(',') if x.strip()]
            except ValueError:
                user.unit_ids = []
        if commit:
            user.save()
        return user


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
        try:
            return [int(x.strip()) for x in raw.split(',') if x.strip()]
        except ValueError:
            raise forms.ValidationError("Please enter valid numeric unit IDs.")
        
        
        
from django import forms
from .models import Company, Device, User


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ['name', 'location', 'description', 'contact_email', 'contact_phone']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'fi', 'placeholder': 'e.g. Acme Industries'}),
            'location': forms.TextInput(attrs={'class': 'fi', 'placeholder': 'e.g. Mumbai, Maharashtra'}),
            'description': forms.Textarea(attrs={'class': 'fi', 'rows': 3, 'placeholder': 'Optional description…'}),
            'contact_email': forms.EmailInput(attrs={'class': 'fi', 'placeholder': 'contact@company.com'}),
            'contact_phone': forms.TextInput(attrs={'class': 'fi', 'placeholder': '+91 98000 00000'}),
        }


class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = ['unit_id', 'name', 'description', 'location', 'company', 'assigned_users', 'is_active']
        widgets = {
            'unit_id': forms.NumberInput(attrs={'class': 'fi', 'placeholder': 'e.g. 100'}),
            'name': forms.TextInput(attrs={'class': 'fi', 'placeholder': 'e.g. Main Panel Meter'}),
            'description': forms.Textarea(attrs={'class': 'fi', 'rows': 3}),
            'location': forms.TextInput(attrs={'class': 'fi', 'placeholder': 'e.g. Building A, Floor 2'}),
            'company': forms.Select(attrs={'class': 'fi'}),
            'assigned_users': forms.SelectMultiple(attrs={'class': 'fi', 'size': 6}),
            'is_active': forms.CheckboxInput(attrs={'class': 'fi-check'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_users'].queryset = User.objects.all().order_by('username')
        self.fields['company'].empty_label = '— No Company —'
        self.fields['assigned_users'].required = False


class AssignDeviceToCompanyForm(forms.Form):
    """Quick form to move a device to a company from the company detail page."""
    unit_id = forms.IntegerField(
        widget=forms.NumberInput(attrs={'class': 'fi', 'placeholder': 'Unit ID'}),
        label='Unit ID'
    )
    name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'fi', 'placeholder': 'Device name (optional)'}),
    )