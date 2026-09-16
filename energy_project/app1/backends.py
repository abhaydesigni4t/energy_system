from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

UserModel = get_user_model()


class EmailBackend(ModelBackend):
    """
    Authenticates against email+password instead of username+password.
    Django's authenticate() always passes the credential as `username`,
    so we treat that kwarg as an email address here.
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        email = username or kwargs.get('email')
        if email is None or password is None:
            return None
        try:
            user = UserModel.objects.get(email__iexact=email)
        except UserModel.DoesNotExist:
            # Run the hasher anyway to avoid leaking which emails exist via timing
            UserModel().set_password(password)
            return None
        except UserModel.MultipleObjectsReturned:
            user = UserModel.objects.filter(email__iexact=email).order_by('id').first()
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None