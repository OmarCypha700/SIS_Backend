"""
Signing in with the device's fingerprint, face or PIN, using passkeys (WebAuthn).

* A person signed in with their password adds a passkey on a device (Profile). The device keeps
  the private key behind its own biometric check; only the public key is stored here.
* On the sign-in page, "Sign in with fingerprint or face" asks the device for a passkey for this
  portal; the server checks its signature and signs the person in. No email or password is typed.
* Passkeys belong to the portal's web address (WEBAUTHN_RP_ID, from FRONTEND_URL) and only work
  there, so they cannot be phished. User verification (the biometric or PIN) is required.
* Each challenge is used once and expires after five minutes; it is kept in the session.
"""
import hashlib
import hmac
import json
import time

from django.conf import settings
from django.utils import timezone
from webauthn import (
    generate_authentication_options, generate_registration_options, options_to_json,
    verify_authentication_response, verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorAttachment, AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement,
    UserVerificationRequirement,
)

from apps.audit.services import log_action
from apps.institutions.models import InstitutionProfile
from common.exceptions import DomainError

from .models import Passkey, User

CHALLENGE_SECONDS = 5 * 60
MAX_PASSKEYS = 10
REGISTER_KEY = "webauthn:register"
SIGN_IN_KEY = "webauthn:sign-in"


def _user_handle(user):
    """A stable, opaque id for the person on their devices (not their database id or email)."""
    return hmac.new(settings.SECRET_KEY.encode(), f"passkey-user:{user.pk}".encode(), hashlib.sha256).digest()


def _remember(request, key, challenge):
    request.session[key] = {"challenge": bytes_to_base64url(challenge), "at": int(time.time())}


def _take(request, key):
    """The pending challenge, used once."""
    saved = request.session.pop(key, None)
    if not saved or time.time() - saved.get("at", 0) > CHALLENGE_SECONDS:
        raise DomainError("This request has expired. Please try again.", code="passkey_expired")
    return base64url_to_bytes(saved["challenge"])


def _parse(credential):
    if isinstance(credential, str):
        try:
            credential = json.loads(credential)
        except ValueError:
            credential = None
    if not isinstance(credential, dict) or not credential.get("id"):
        raise DomainError("The device's answer could not be read. Please try again.", code="passkey_invalid")
    return credential


def registration_options(request):
    user = request.user
    if user.passkeys.count() >= MAX_PASSKEYS:
        raise DomainError(f"You can have at most {MAX_PASSKEYS} passkeys. Remove one first.", code="too_many_passkeys")
    options = generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=InstitutionProfile.load().name or "Student Portal",
        user_id=_user_handle(user),
        user_name=user.email,
        user_display_name=user.get_full_name() or user.email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            # The device's own fingerprint, face or PIN, as a passkey that needs no typed email.
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id))
                             for p in user.passkeys.all()],
    )
    _remember(request, REGISTER_KEY, options.challenge)
    return json.loads(options_to_json(options))


def register(request, credential, name=""):
    credential = _parse(credential)
    try:
        verified = verify_registration_response(
            credential=credential, expected_challenge=_take(request, REGISTER_KEY),
            expected_rp_id=settings.WEBAUTHN_RP_ID, expected_origin=settings.WEBAUTHN_ORIGINS,
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as exc:
        raise DomainError(f"The passkey could not be set up: {exc}", code="passkey_invalid") from None
    credential_id = bytes_to_base64url(verified.credential_id)
    if Passkey.objects.filter(credential_id=credential_id).exists():
        raise DomainError("This passkey is already set up.", code="passkey_exists", status_code=409)
    transports = (credential.get("response") or {}).get("transports") or []
    passkey = Passkey.objects.create(
        user=request.user, credential_id=credential_id, public_key=verified.credential_public_key,
        sign_count=verified.sign_count, transports=[str(t) for t in transports][:8],
        name=(name or "").strip()[:80] or "This device")
    log_action("auth.passkey_added", actor=request.user, instance=request.user,
               summary=f"Added a passkey ({passkey.name})")
    return passkey


def sign_in_options(request):
    options = generate_authentication_options(rp_id=settings.WEBAUTHN_RP_ID,
                                              user_verification=UserVerificationRequirement.REQUIRED)
    _remember(request, SIGN_IN_KEY, options.challenge)
    return json.loads(options_to_json(options))


GENERIC_FAILURE = "Signing in with this passkey did not work. Sign in with your password instead."


def authenticate(request, credential):
    """The user the passkey belongs to, after checking the device's signature. Raises DomainError."""
    credential = _parse(credential)
    challenge = _take(request, SIGN_IN_KEY)
    passkey = Passkey.objects.select_related("user").filter(credential_id=credential["id"]).first()
    if passkey is None or not passkey.user.is_active:
        # Removed on the server but still on the device, or the account was disabled.
        raise DomainError(GENERIC_FAILURE, code="passkey_unknown", status_code=400)
    try:
        verified = verify_authentication_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGINS, credential_public_key=bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count, require_user_verification=True,
        )
    except InvalidAuthenticationResponse:
        raise DomainError(GENERIC_FAILURE, code="passkey_invalid") from None
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = timezone.now()
    passkey.save(update_fields=["sign_count", "last_used_at"])
    log_action("auth.passkey_sign_in", actor=passkey.user, instance=passkey.user,
               summary=f"Signed in with a passkey ({passkey.name})")
    return passkey.user


def remove(user, passkey_id, *, actor):
    passkey = Passkey.objects.filter(user=user, pk=passkey_id).first()
    if passkey is None:
        raise DomainError("Passkey not found.", code="not_found", status_code=404)
    passkey.delete()
    log_action("auth.passkey_removed", actor=actor, instance=user, summary=f"Removed a passkey ({passkey.name})")
