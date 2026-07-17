import json

from django.contrib import messages
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from .forms import RegisterForm, LoginForm, ProfileUpdateForm, PreferencesForm
from .models import Profile


def _get_or_create_profile(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def register_view(request):
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            _get_or_create_profile(user)
            login(request, user)
            return redirect('chat:chat_home')
    else:
        form = RegisterForm()
    return render(request, 'accounts/register.html', {'form': form})


def login_view(request):
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user)
                return redirect('chat:chat_home')
            messages.error(request, 'Invalid credentials')
    else:
        form = LoginForm()
    return render(request, 'accounts/login.html', {'form': form})


@login_required
def logout_view(request):
    logout(request)
    return redirect('accounts:login')


@login_required
def profile_view(request):
    profile = _get_or_create_profile(request.user)

    if request.method == 'POST':
        action = request.POST.get('action', 'update')

        if action == 'delete_avatar':
            if profile.avatar:
                profile.avatar.delete(save=False)
                profile.avatar = None
                profile.save()
                messages.success(request, 'Profile picture removed.')
            return redirect('accounts:profile')

        if action == 'delete_account':
            request.user.delete()
            messages.success(request, 'Your account has been deleted.')
            return redirect('accounts:login')

        form = ProfileUpdateForm(request.POST, request.FILES, instance=profile, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated successfully!')
            return redirect('accounts:profile')
        messages.error(request, 'Please fix the errors below.')
    else:
        form = ProfileUpdateForm(instance=profile, user=request.user)

    chat_count = request.user.chat_sessions.count()
    message_count = sum(s.messages.count() for s in request.user.chat_sessions.all())

    from chat.models import FileAttachment
    file_count = FileAttachment.objects.filter(
        message__session__user=request.user
    ).count()

    return render(request, 'accounts/profile.html', {
        'form': form,
        'profile': profile,
        'chat_count': chat_count,
        'message_count': message_count,
        'file_count': file_count,
    })


@login_required
@require_POST
def save_preferences(request):
    """Save advanced mode and bubble colors from sidebar settings."""
    profile = _get_or_create_profile(request.user)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    prefs = {
        'advanced_mode': bool(data.get('advanced_mode', profile.advanced_mode)),
        'user_bubble_color': str(data.get('user_bubble_color', profile.user_bubble_color or ''))[:100],
        'ai_bubble_color': str(data.get('ai_bubble_color', profile.ai_bubble_color or ''))[:100],
        'avatar_glow_color': str(data.get('avatar_glow_color', profile.avatar_glow_color or '#8b5cf6'))[:20],
    }

    for key, value in prefs.items():
        setattr(profile, key, value)
    profile.save()

    return JsonResponse({
        'success': True,
        'advanced_mode': profile.advanced_mode,
        'user_bubble_color': profile.user_bubble_color,
        'ai_bubble_color': profile.ai_bubble_color,
        'avatar_glow_color': profile.avatar_glow_color,
    })


@login_required
def get_preferences(request):
    """Return user preferences for sidebar settings."""
    profile = _get_or_create_profile(request.user)
    return JsonResponse({
        'advanced_mode': profile.advanced_mode,
        'user_bubble_color': profile.user_bubble_color,
        'ai_bubble_color': profile.ai_bubble_color,
        'avatar_glow_color': profile.avatar_glow_color,
    })
