from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm

from .models import Profile


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']


class LoginForm(forms.Form):
    username = forms.CharField()
    password = forms.CharField(widget=forms.PasswordInput)


class ProfileUpdateForm(forms.ModelForm):
    display_name = forms.CharField(max_length=100, required=False)
    email = forms.EmailField(required=False)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)

    class Meta:
        model = Profile
        fields = ['avatar', 'bio', 'display_name', 'avatar_glow_color']
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Tell us about yourself...'}),
            'avatar_glow_color': forms.TextInput(attrs={'type': 'color'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        super().__init__(*args, **kwargs)
        self.fields['email'].initial = self.user.email
        self.fields['first_name'].initial = self.user.first_name
        self.fields['last_name'].initial = self.user.last_name
        self.fields['avatar'].widget.attrs.update({'accept': 'image/*'})

    def save(self, commit=True):
        profile = super().save(commit=False)
        self.user.email = self.cleaned_data.get('email', self.user.email)
        self.user.first_name = self.cleaned_data.get('first_name', '')
        self.user.last_name = self.cleaned_data.get('last_name', '')
        if commit:
            self.user.save()
            profile.save()
        return profile


class PreferencesForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['advanced_mode', 'user_bubble_color', 'ai_bubble_color', 'avatar_glow_color']
