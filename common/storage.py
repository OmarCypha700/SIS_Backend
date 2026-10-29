"""
Private file storage for files that must never be publicly downloadable (the signature and seal
printed on official transcripts). It lives outside MEDIA_ROOT, has no URL, and is only read by the
server when it builds a document or by authorised staff through an authenticated endpoint.
"""
from django.conf import settings
from django.core.files.storage import FileSystemStorage


def private_storage():
    return FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT, base_url=None)
