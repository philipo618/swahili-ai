import json
import mimetypes
import re
import threading
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import get_valid_filename
from django.utils import timezone

from accounts.models import UserMemory
from .file_utils import extract_office_text
from .models import ChatSession, FileAttachment, Message
from .sample_questions import get_sample_questions

try:
    from google import genai as google_genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover - optional dependency
    google_genai = None
    genai_types = None

# Current Gemini models, ordered for reliable chat responses. Gemini 2.0
# models were shut down by Google, so they are not valid fallbacks.
GEMINI_MODEL_CANDIDATES = [
    getattr(settings, 'GEMINI_MODEL', '') or 'gemini-3.5-flash',
    'gemini-3.5-flash',
    'gemini-3.1-flash-lite',
    'gemini-2.5-flash-lite',
]

# Deduplicate while preserving order
_seen = set()
GEMINI_MODEL_CANDIDATES = [
    m for m in GEMINI_MODEL_CANDIDATES
    if m and not (m in _seen or _seen.add(m))
]

_clients = {}
_client_error = None
_client_lock = threading.Lock()


def _get_api_keys():
    """Return configured keys without ever exposing them to users or logs."""
    keys = getattr(settings, 'GEMINI_API_KEYS', ())
    if isinstance(keys, str):
        keys = keys.split(',')
    keys = [key.strip() for key in keys if key and key.strip()]
    if not keys:
        legacy_key = (getattr(settings, 'GEMINI_API_KEY', '') or '').strip()
        keys = [legacy_key] if legacy_key else []
    return tuple(dict.fromkeys(keys))


def get_gemini_client(api_key):
    """Return a cached client for one key; keep initialization errors private."""
    global _client_error

    if google_genai is None:
        _client_error = (
            "The google-genai package is not installed. "
            "Run: pip install google-genai"
        )
        return None

    if not api_key:
        _client_error = (
            "Gemini is not configured yet. "
            "Please add a valid GEMINI_API_KEY to your .env file "
            "(get one at https://aistudio.google.com/apikey)."
        )
        return None

    with _client_lock:
        if api_key in _clients:
            return _clients[api_key]
        try:
            client = google_genai.Client(api_key=api_key)
            _clients[api_key] = client
            _client_error = None
            return client
        except Exception:  # pragma: no cover
            _client_error = "Could not initialize Gemini. Check the configured API keys."
            return None


def _is_auth_error(error_text: str) -> bool:
    text = error_text.lower()
    markers = (
        'api key',
        'api_key',
        'invalid',
        'permission',
        'unauthorized',
        'unauthenticated',
        '403',
        '401',
        'expired',
    )
    return any(m in text for m in markers)


def _is_model_error(error_text: str) -> bool:
    text = error_text.lower()
    markers = (
        'not found',
        'not_found',
        'not supported',
        'unsupported',
        'no longer available',
        'is not found',
    )
    return any(m in text for m in markers)


def _is_quota_error(error_text: str) -> bool:
    text = error_text.lower()
    markers = (
        'quota',
        'resource_exhausted',
        'rate limit',
        'rate_limit',
        '429',
        'exceeded your current quota',
    )
    return any(m in text for m in markers)


def _is_transient_error(error_text: str) -> bool:
    text = error_text.lower()
    markers = (
        'unavailable',
        'high demand',
        'try again',
        'resource_exhausted',
        '429',
        '503',
        '500',
        'overloaded',
    )
    return any(m in text for m in markers)


def generate_ai_reply(contents):
    """Call Gemini with model fallback and safe key failover."""
    api_keys = _get_api_keys()
    if not api_keys:
        return _client_error or (
            "Gemini is not configured yet. "
            "Please add GEMINI_API_KEY or GEMINI_API_KEYS to your .env file."
        )

    last_error = None
    saw_quota_error = False
    for api_key in api_keys:
        client = get_gemini_client(api_key)
        if client is None:
            continue
        for model_name in GEMINI_MODEL_CANDIDATES:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                )
                text = getattr(response, 'text', None)
                if text:
                    return text.strip()
                return 'No response returned.'
            except Exception as exc:
                last_error = str(exc)
                if _is_auth_error(last_error):
                    break  # rejected: fail over to the next key
                if _is_quota_error(last_error):
                    saw_quota_error = True
                    continue  # another model or key may still have capacity
                if _is_model_error(last_error) or _is_transient_error(last_error):
                    continue  # another model may work with this key
                break  # keep provider details private and try the next key

    if saw_quota_error and (not last_error or _is_quota_error(last_error)):
        configured_key_label = "key" if len(api_keys) == 1 else "keys"
        return (
            f"Gemini API quota is exhausted for the configured {configured_key_label}. "
            "Please wait a minute and try again."
        )

    if last_error and _is_transient_error(last_error):
        return (
            "Gemini is busy right now (high demand). "
            "Please wait a few seconds and try again."
        )

    return (
        "Gemini is temporarily unavailable for the configured keys. "
        "Please try again shortly."
    )


def _read_file_for_context(file_path: str, filename: str, max_bytes: int = 5 * 1024 * 1024):
    """Read file bytes/text for inclusion in follow-up chat context."""
    abs_path = Path(settings.MEDIA_ROOT) / file_path
    if not abs_path.exists() and hasattr(default_storage, 'path'):
        try:
            abs_path = Path(default_storage.path(file_path))
        except Exception:
            return None

    if not abs_path.exists():
        return None

    mime = _guess_mime(filename)
    try:
        data = abs_path.read_bytes()
        if len(data) > max_bytes:
            return None

        if mime.startswith('text/') or filename.lower().endswith(('.txt', '.csv', '.md', '.json')):
            return data.decode('utf-8', errors='ignore')[:8000]

        if mime.startswith('image/') or mime == 'application/pdf':
            if genai_types is not None:
                return genai_types.Part.from_bytes(data=data, mime_type=mime)
    except Exception:
        pass
    return None


def _remember_user_preferences(user, message: str):
    """Save only preferences the user explicitly states, never inferred traits."""
    try:
        memory, created = UserMemory.objects.get_or_create(user=user)
    except DatabaseError:
        return  # The chat remains usable until the memory migration is applied.

    # On first use, learn from recent existing messages so old chats are useful.
    messages_to_review = [message]
    if created:
        messages_to_review = list(
            Message.objects.filter(session__user=user, is_ai=False)
            .order_by('-created_at')
            .values_list('content', flat=True)[:60]
        )

    preferences = list(memory.preferences or [])
    for text in messages_to_review:
        lower = text.lower()
        swahili_markers = (' na ', ' kwa ', ' nina', ' nataka', ' tafadhali', ' habari', ' leo ', ' siku ')
        if any(marker in f' {lower} ' for marker in swahili_markers):
            memory.preferred_language = 'Swahili'
        elif re.search(r'\b(the|and|please|what|how|thank)\b', lower):
            memory.preferred_language = 'English'

        matches = re.findall(
            r'(?:\bnapenda\b|\bsipendi\b|\bninapendelea\b|\bprefer\b|\bi prefer\b)\s+([^.!?\n]{2,120})',
            text,
            flags=re.IGNORECASE,
        )
        for preference in matches:
            preference = preference.strip()
            if preference and preference not in preferences:
                preferences.append(preference)
    memory.preferences = preferences[-12:]
    memory.save()


def _user_memory_context(user):
    """Return compact preference context for a user across all chat sessions."""
    try:
        memory = user.ai_memory
    except (UserMemory.DoesNotExist, DatabaseError):
        return 'No saved preferences yet.'

    notes = []
    if memory.preferred_language:
        notes.append(f"Preferred response language: {memory.preferred_language}.")
    if memory.preferences:
        notes.append('Explicit preferences: ' + '; '.join(memory.preferences[-8:]) + '.')
    return ' '.join(notes) if notes else 'No saved preferences yet.'


def _build_chat_contents(session, user_message: str, current_message_id=None):
    """Include session context, explicit preferences, and the real local time."""
    history_lines = []
    recent = session.messages.order_by('-created_at')
    if current_message_id:
        recent = recent.exclude(id=current_message_id)
    recent = recent[:16]
    for msg in reversed(list(recent)):
        role = 'Assistant' if msg.is_ai else 'User'
        history_lines.append(f"{role}: {msg.content}")

    attachments = FileAttachment.objects.filter(
        message__session=session
    ).select_related('message').order_by('-uploaded_at')[:3]

    parts = []
    attachment_names = []

    for att in attachments:
        attachment_names.append(att.filename)
        file_ctx = _read_file_for_context(att.file.name, att.filename)
        if file_ctx is not None:
            if isinstance(file_ctx, str):
                parts.append(
                    f"[File: {att.filename}]\n{file_ctx[:6000]}"
                )
            else:
                parts.append(file_ctx)

    attachment_note = ''
    if attachment_names:
        attachment_note = (
            f"\n\nFiles uploaded in this chat: {', '.join(attachment_names)}. "
            "Use the file content below to answer questions about them."
        )

    now = timezone.localtime()
    date_context = (
        f"Current local date and time is {now.strftime('%A, %d %B %Y, %H:%M')} "
        f"in {timezone.get_current_timezone_name()} (ISO date: {now.date().isoformat()}). "
        "Use this as the source of truth for questions about today, dates, or time; do not guess."
    )
    text_prompt = (
        "You are PD.AI, a helpful smart assistant. "
        "Answer clearly and concisely. If the user asks about uploaded files "
        "(images, PDFs, documents), use the file content provided. "
        "Respect the user's explicit preferences, but never invent personal facts.\n\n"
        f"{date_context}\n"
        f"Saved user preferences from earlier chats: {_user_memory_context(session.user)}\n\n"
        f"Conversation so far:\n{chr(10).join(history_lines) if history_lines else '(empty)'}"
        f"{attachment_note}\n\n"
        f"User: {user_message}\nAssistant:"
    )

    if parts:
        return parts + [text_prompt]
    return text_prompt


def _guess_mime(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or 'application/octet-stream'


def _unique_storage_name(original_name: str) -> str:
    safe = get_valid_filename(original_name) or 'upload'
    return f'chat_files/{uuid.uuid4().hex}_{safe}'


def _analyze_uploaded_file(file_path: str, filename: str, user_description: str = ''):
    """Ask Gemini to analyze an uploaded file together with the user's message."""
    if not _get_api_keys():
        return (
            f"File '{filename}' saved. "
            + (_client_error or "AI analysis is pending until Gemini is configured.")
        )

    abs_path = Path(settings.MEDIA_ROOT) / file_path
    if not abs_path.exists():
        # default_storage may use a relative path already under MEDIA_ROOT
        abs_path = Path(default_storage.path(file_path)) if hasattr(default_storage, 'path') else abs_path

    mime = _guess_mime(filename)
    supported_prefixes = ('image/', 'audio/', 'video/', 'text/')
    supported_exact = {
        'application/pdf',
        'application/json',
        'text/plain',
        'text/csv',
        'text/markdown',
    }

    desc_block = ''
    if user_description.strip():
        desc_block = (
            f"\n\nThe user's message about this file:\n{user_description.strip()}\n\n"
            "Answer their question or respond to their description using the file content."
        )
    else:
        desc_block = (
            "\n\nBriefly confirm you received the file and summarize or describe "
            "its contents so you can answer follow-up questions."
        )

    can_send_bytes = mime.startswith(supported_prefixes) or mime in supported_exact

    try:
        if can_send_bytes and abs_path.exists() and genai_types is not None:
            data = abs_path.read_bytes()
            if len(data) <= 15 * 1024 * 1024:
                contents = [
                    genai_types.Part.from_bytes(data=data, mime_type=mime),
                    f"The user uploaded a file named '{filename}'.{desc_block}",
                ]
                return generate_ai_reply(contents)
    except Exception:
        pass

    # Office documents: extract text locally, then send to Gemini
    office_text = extract_office_text(abs_path, filename)
    if office_text:
        return generate_ai_reply(
            f"The user uploaded '{filename}' with this extracted content:\n\n"
            f"{office_text[:12000]}{desc_block}"
        )

    # Fallback: plain text files
    if mime.startswith('text/') or filename.lower().endswith(('.txt', '.csv', '.md', '.json')):
        try:
            text = abs_path.read_text(encoding='utf-8', errors='ignore')[:12000]
            return generate_ai_reply(
                f"The user uploaded '{filename}' with this content:\n\n{text}{desc_block}"
            )
        except Exception:
            pass

    prompt = (
        f"The user uploaded a file named '{filename}' (type: {mime})."
        f"{desc_block if user_description.strip() else chr(10) + 'Confirm the upload was received. If you cannot read this file type directly, say so and offer to help based on what the user tells you about it.'}"
    )
    return generate_ai_reply(prompt)


@login_required
def chat_home(request):
    session_id = request.GET.get('session')
    if session_id:
        session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    else:
        session = ChatSession.objects.filter(user=request.user).first()
        if not session:
            session = ChatSession.objects.create(user=request.user, title='New Chat')

    sessions = ChatSession.objects.filter(user=request.user).order_by('-updated_at')
    messages_qs = session.messages.all().order_by('created_at')

    user_avatar_url = None
    profile = None
    try:
        profile = request.user.profile
    except Exception:
        profile = None

    if profile is None:
        from accounts.models import Profile
        profile, _ = Profile.objects.get_or_create(user=request.user)

    if profile and profile.avatar:
        user_avatar_url = profile.avatar.url

    context = {
        'active_session': session,
        'sessions': sessions,
        'messages': messages_qs,
        'user': request.user,
        'user_avatar_url': user_avatar_url,
        'profile': profile,
        'avatar_glow_color': profile.avatar_glow_color if profile else '#8b5cf6',
        'advanced_mode': profile.advanced_mode if profile else False,
        'user_bubble_color': profile.user_bubble_color if profile else '',
        'ai_bubble_color': profile.ai_bubble_color if profile else '',
        'sample_questions': get_sample_questions(request.user),
    }
    return render(request, 'chat/chat.html', context)


@login_required
def sample_questions_api(request):
    """JSON endpoint for refreshed sample prompts (changes daily / by usage)."""
    return JsonResponse({
        'questions': get_sample_questions(request.user),
    })


@login_required
def send_message(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=405)

    data = json.loads(request.body)
    user_message = data.get('message', '').strip()
    session_id = data.get('session_id')

    if not user_message:
        return JsonResponse({'error': 'Message is empty'}, status=400)

    session = get_object_or_404(ChatSession, id=session_id, user=request.user)

    user_record = Message.objects.create(session=session, content=user_message, is_ai=False)
    _remember_user_preferences(request.user, user_message)

    contents = _build_chat_contents(session, user_message, current_message_id=user_record.id)
    ai_reply = generate_ai_reply(contents)

    Message.objects.create(session=session, content=ai_reply, is_ai=True)

    if session.messages.count() == 2:
        session.title = user_message[:50]
        session.save()

    return JsonResponse({
        'ai_response': ai_reply,
        'session_title': session.title,
    })


@login_required
def upload_file(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=405)

    session_id = request.POST.get('session_id')
    file = request.FILES.get('file')
    description = (request.POST.get('description') or '').strip()

    if not file:
        return JsonResponse({'error': 'No file provided'}, status=400)

    session = get_object_or_404(ChatSession, id=session_id, user=request.user)

    if description:
        user_content = f"📎 {file.name}\n\n{description}"
    else:
        user_content = f"📎 {file.name}"

    user_msg = Message.objects.create(
        session=session,
        content=user_content,
        is_ai=False,
    )

    file_name = default_storage.save(_unique_storage_name(file.name), file)
    file_url = default_storage.url(file_name)

    FileAttachment.objects.create(
        message=user_msg,
        file=file_name,
        filename=file.name,
    )

    ai_reply = _analyze_uploaded_file(file_name, file.name, description)
    Message.objects.create(session=session, content=ai_reply, is_ai=True)

    if session.messages.count() <= 2 and session.title == 'New Chat':
        session.title = f"File: {file.name[:40]}"
        session.save()

    return JsonResponse({
        'filename': file.name,
        'file_url': file_url,
        'file_content': ai_reply,
        'session_title': session.title,
    })


@login_required
def rename_chat(request, session_id):
    if request.method == 'POST':
        session = get_object_or_404(ChatSession, id=session_id, user=request.user)
        new_title = request.POST.get('title', '').strip()
        if new_title:
            session.title = new_title
            session.save()
            messages.success(request, 'Chat renamed successfully!')
        else:
            messages.error(request, 'Title cannot be empty.')
    return redirect('chat:chat_home')


@login_required
def delete_chat(request, session_id):
    if request.method == 'POST':
        session = get_object_or_404(ChatSession, id=session_id, user=request.user)
        session.delete()
        messages.success(request, 'Chat deleted.')
    return redirect('chat:chat_home')


@login_required
def new_chat(request):
    session = ChatSession.objects.create(user=request.user, title='New Chat')
    return redirect(f"/?session={session.id}")
