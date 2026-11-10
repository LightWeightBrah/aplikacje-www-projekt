"""Nagrywanie rozmów: zapis nagrań z dialera na Dysk Google i odsłuch w CRM (tylko manager).

Całość jest opcjonalna: bez włączenia w Ustawieniach i bez połączonego konta Google telefony
nic nie nagrywają, a reszta CRM działa jak zawsze.

Dysk Google: manager raz łączy konto (OAuth, zakres drive.file – CRM widzi wyłącznie pliki,
które sam utworzył). Pliki trafiają do folderu „Solid CRM – nagrania”, w podfoldery agentów
(„Jan Kowalski (agent1)”), a w nich miesięcy (2026-11).
Do Google mówimy zwykłym HTTP (urllib), bez dodatkowych bibliotek.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.utils import timezone

from .models import CallRecording, CallRequest, RecordingConfig

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id"
SCOPES = "https://www.googleapis.com/auth/drive.file openid email"
FOLDER_MIME = "application/vnd.google-apps.folder"
ROOT_FOLDER_NAME = "Solid CRM – nagrania"
CALLBACK_PATH = "/crm/recordings/google/callback/"
STATE_SALT = "crm-google-drive"
STATE_MAX_AGE = 15 * 60
TOKEN_CACHE_KEY = "crm-google-access-token"
# zapamiętane id folderów (agent, miesiąc); zmiana wersji = zapomnij wszystkie (np. ktoś skasował folder z Dysku)
FOLDERS_VERSION_KEY = "crm-drive-folders-version"
# ile starych nagrań usuwamy przy jednym nowym (żeby wgrywanie nie trwało za długo)
PURGE_BATCH = 10
# nagranie można dosłać do kilku dni po rozmowie (telefon bez internetu wyśle je później)
UPLOAD_WINDOW = timedelta(days=7)


class DriveError(Exception):
    """Błąd po stronie Dysku Google (komunikat po polsku dla managera)."""


def oauth_configured():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def redirect_uri(request):
    return request.build_absolute_uri(CALLBACK_PATH)


# ------------------------------------------------------------------ HTTP


def _http(method, url, data=None, headers=None, timeout=30):
    """Zapytanie do Google: zwraca (status, bajty). Błąd sieci → DriveError."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise DriveError(f"Brak połączenia z Google ({e}).") from e


def _json(body):
    try:
        return json.loads(body or b"{}")
    except ValueError:
        return {}


def _form(url, fields):
    status, body = _http(
        "POST", url, urllib.parse.urlencode(fields).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    return status, _json(body)


# ------------------------------------------------------------------ łączenie konta


def auth_url(request, user):
    state = signing.dumps({"u": user.pk}, salt=STATE_SALT)
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri(request),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def user_from_state(state):
    """Kto zaczął łączenie (podpisany parametr state, ważny 15 min) albo None."""
    from django.contrib.auth import get_user_model

    try:
        data = signing.loads(state or "", salt=STATE_SALT, max_age=STATE_MAX_AGE)
    except signing.BadSignature:
        return None
    return get_user_model().objects.filter(pk=data.get("u"), is_active=True).first()


def connect(request, user, code):
    """Wymienia kod z Google na token, zakłada folder na nagrania i zapisuje połączenie."""
    status, data = _form(TOKEN_URL, {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri(request),
        "grant_type": "authorization_code",
    })
    if status != 200 or not data.get("refresh_token"):
        raise DriveError("Google nie wydał dostępu do Dysku. Spróbuj połączyć jeszcze raz.")
    scopes = data.get("scope", "")
    if "drive.file" not in scopes:
        raise DriveError("Nie zaznaczono zgody na Dysk Google. Połącz jeszcze raz i zostaw zaznaczony Dysk.")
    access = data["access_token"]
    cache.set(TOKEN_CACHE_KEY, access, max(int(data.get("expires_in", 3600)) - 120, 60))
    st, info = _http("GET", USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
    email = _json(info).get("email", "") if st == 200 else ""

    config = RecordingConfig.get()
    old_token = config.google_refresh_token
    config.google_refresh_token = data["refresh_token"]
    config.google_email = email
    config.folder_id = ""
    config.connected_at = timezone.now()
    config.updated_by = user
    config.save()
    _root_folder(config)
    if old_token and old_token != config.google_refresh_token:
        _revoke(old_token)
    return config


def disconnect(config, user):
    token = config.google_refresh_token
    config.google_refresh_token = ""
    config.google_email = ""
    config.folder_id = ""
    config.connected_at = None
    config.updated_by = user
    config.save()
    cache.delete(TOKEN_CACHE_KEY)
    if token:
        _revoke(token)


def _revoke(token):
    try:
        _form(REVOKE_URL, {"token": token})
    except DriveError:
        pass   # i tak zapominamy token po naszej stronie


def _access_token(config):
    token = cache.get(TOKEN_CACHE_KEY)
    if token:
        return token
    if not config.google_refresh_token:
        raise DriveError("Dysk Google nie jest połączony.")
    status, data = _form(TOKEN_URL, {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "refresh_token": config.google_refresh_token,
        "grant_type": "refresh_token",
    })
    if status != 200 or "access_token" not in data:
        if data.get("error") == "invalid_grant":
            raise DriveError("Google cofnął dostęp do Dysku (np. zmiana hasła albo aplikacja w trybie testowym). "
                             "Połącz Dysk Google ponownie w Ustawieniach.")
        raise DriveError("Nie udało się zalogować do Dysku Google.")
    cache.set(TOKEN_CACHE_KEY, data["access_token"], max(int(data.get("expires_in", 3600)) - 120, 60))
    return data["access_token"]


def _api(config, method, url, data=None, headers=None, retry=True):
    hdrs = {"Authorization": f"Bearer {_access_token(config)}", **(headers or {})}
    status, body = _http(method, url, data, hdrs, timeout=60)
    if status == 401 and retry:
        cache.delete(TOKEN_CACHE_KEY)
        return _api(config, method, url, data, headers, retry=False)
    return status, body


# ------------------------------------------------------------------ foldery i pliki


def _create_folder(config, name, parent=None):
    meta = {"name": name, "mimeType": FOLDER_MIME}
    if parent:
        meta["parents"] = [parent]
    status, body = _api(config, "POST", FILES_URL + "?fields=id", json.dumps(meta).encode(),
                        {"Content-Type": "application/json"})
    if status != 200:
        raise DriveError(f"Nie udało się założyć folderu na Dysku Google ({status}).")
    return _json(body)["id"]


def _folder_alive(config, folder_id):
    status, body = _api(config, "GET", f"{FILES_URL}/{folder_id}?fields=id,trashed")
    return status == 200 and not _json(body).get("trashed")


def _root_folder(config):
    if config.folder_id and _folder_alive(config, config.folder_id):
        return config.folder_id
    config.folder_id = _create_folder(config, ROOT_FOLDER_NAME)
    RecordingConfig.objects.filter(pk=config.pk).update(folder_id=config.folder_id)
    return config.folder_id


def _child_folder(config, parent, name):
    """Podfolder o tej nazwie w folderze `parent` (szuka, a gdy go nie ma – zakłada)."""
    version = cache.get(FOLDERS_VERSION_KEY, 0)
    key = f"crm-drive-folder-{version}-" + uuid.uuid5(uuid.NAMESPACE_URL, f"{parent}/{name}").hex
    cached = cache.get(key)
    if cached:
        return cached
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name = '{escaped}' and '{parent}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false"
    status, body = _api(config, "GET", FILES_URL + "?" + urllib.parse.urlencode({"q": q, "fields": "files(id)"}))
    files = _json(body).get("files", []) if status == 200 else []
    folder = files[0]["id"] if files else _create_folder(config, name, parent)
    cache.set(key, folder, 24 * 3600)
    return folder


def agent_folder_name(user):
    """Folder agenta: „Jan Kowalski (agent1)” – login na końcu, żeby dwóch Janów Kowalskich się nie mieszało."""
    full = _safe(user.get_full_name())
    return f"{full} ({user.username})" if full else user.username


def _target_folder(config, root, agent, when):
    """Solid CRM – nagrania / Jan Kowalski (agent1) / 2026-11"""
    folder = _child_folder(config, root, agent_folder_name(agent)) if agent else root
    return _child_folder(config, folder, timezone.localtime(when).strftime("%Y-%m"))


def _upload(config, name, data, content_type, parent):
    boundary = "solidcrm" + uuid.uuid4().hex
    meta = json.dumps({"name": name, "parents": [parent]}).encode()
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta
        + f"\r\n--{boundary}\r\nContent-Type: {content_type}\r\n\r\n".encode() + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, resp = _api(config, "POST", UPLOAD_URL, body, {"Content-Type": f"multipart/related; boundary={boundary}"})
    if status != 200:
        return None, status
    return _json(resp).get("id"), status


def upload_file(config, name, data, content_type, when, agent=None):
    """Wgrywa plik do folderu agenta i miesiąca; gdy ktoś usunął folder z Dysku – zakłada go na nowo."""
    for attempt in range(2):
        root = _root_folder(config)
        target = _target_folder(config, root, agent, when)
        file_id, status = _upload(config, name, data, content_type, target)
        if file_id:
            return file_id
        if status == 404 and attempt == 0:   # folder zniknął: zapominamy zapamiętane foldery i próbujemy jeszcze raz
            cache.set(FOLDERS_VERSION_KEY, cache.get(FOLDERS_VERSION_KEY, 0) + 1, None)
            config.folder_id = ""
            continue
        break
    raise DriveError(f"Dysk Google nie przyjął nagrania ({status}).")


def download_file(config, file_id):
    status, body = _api(config, "GET", f"{FILES_URL}/{file_id}?alt=media")
    if status == 404:
        raise DriveError("Tego nagrania nie ma już na Dysku Google.")
    if status != 200:
        raise DriveError(f"Nie udało się pobrać nagrania z Dysku Google ({status}).")
    return body


def delete_file(config, file_id):
    status, _ = _api(config, "DELETE", f"{FILES_URL}/{file_id}")
    return status in (200, 204, 404)


# ------------------------------------------------------------------ nagrania z dialera


def _safe(text):
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", text or "").strip()[:60]


def file_name(call, started, ext):
    lead = call.lead
    who = lead.display_name if lead else call.phone
    agent = call.user.get_full_name() or call.user.username
    stamp = timezone.localtime(started).strftime("%Y-%m-%d %H-%M")
    return f"{stamp} {_safe(agent)} {_safe(who)} {call.phone}.{ext}"


def save_recording(device, call_id, data, content_type, duration_seconds=None):
    """Telefon wysyła nagranie rozmowy. Zwraca (nagranie, None) albo (None, (status HTTP, komunikat))."""
    config = RecordingConfig.get()
    if not config.active:
        return None, (409, "Nagrywanie rozmów jest wyłączone w CRM.")
    call = CallRequest.objects.select_related("lead", "user").filter(pk=call_id, user=device.user).first()
    if call is None:
        return None, (404, "Nie ma takiego połączenia.")
    if call.created_at < timezone.now() - UPLOAD_WINDOW:
        return None, (410, "Za stare nagranie – nie zapisujemy go.")
    existing = CallRecording.objects.filter(call=call).first()
    if existing:
        return existing, None   # telefon wysłał drugi raz (np. zerwane połączenie) – już mamy
    if not data:
        return None, (400, "Pusty plik nagrania.")
    if len(data) > settings.RECORDING_MAX_MB * 1024 * 1024:
        return None, (413, "Nagranie jest za duże.")
    content_type = content_type if content_type in ("audio/ogg", "audio/mp4", "audio/aac", "audio/mpeg") else "audio/ogg"
    ext = {"audio/ogg": "ogg", "audio/mp4": "m4a", "audio/aac": "aac", "audio/mpeg": "mp3"}[content_type]
    started = call.started_at or call.created_at
    try:
        file_id = upload_file(config, file_name(call, started, ext), data, content_type, started, call.user)
    except DriveError as e:
        return None, (502, str(e))
    seconds = duration_seconds if duration_seconds is not None else call.duration_seconds
    rec = CallRecording.objects.create(
        call=call, lead=call.lead, agent=call.user, phone=call.phone, started_at=started,
        duration_seconds=max(int(seconds or 0), 0), size_bytes=len(data), content_type=content_type,
        drive_file_id=file_id,
    )
    purge_old(config)
    return rec, None


def purge_old(config=None, limit=PURGE_BATCH):
    """Usuwa nagrania starsze niż ustawiona liczba dni (z Dysku i z CRM). Zwraca, ile usunięto."""
    config = config or RecordingConfig.get()
    if not config.drive_connected:
        return 0
    border = timezone.now() - timedelta(days=config.retention_days)
    removed = 0
    for rec in CallRecording.objects.filter(started_at__lt=border).order_by("started_at")[:limit]:
        try:
            if not delete_file(config, rec.drive_file_id):
                continue
        except DriveError:
            break
        rec.delete()
        removed += 1
    return removed


def status_for(config):
    """Stan nagrywania dla strony Ustawień."""
    return {
        "enabled": config.enabled,
        "active": config.active,
        "retention_days": config.retention_days,
        "oauth_configured": oauth_configured(),
        "drive_connected": config.drive_connected,
        "google_email": config.google_email,
        "connected_at": config.connected_at,
        "recordings": CallRecording.objects.count(),
        "updated_at": config.updated_at,
        "updated_by_name": (config.updated_by.get_full_name() or config.updated_by.username) if config.updated_by else None,
    }
