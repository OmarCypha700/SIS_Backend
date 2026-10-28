import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Command(BaseCommand):
    help = "Generate a VAPID key pair for web push. Add the output to .env; keep the private key secret."

    def handle(self, *args, **options):
        key = ec.generate_private_key(ec.SECP256R1())
        private = b64url(key.private_numbers().private_value.to_bytes(32, "big"))
        public = b64url(key.public_key().public_bytes(serialization.Encoding.X962,
                                                      serialization.PublicFormat.UncompressedPoint))
        self.stdout.write(f"VAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY={private}\n"
                          "VAPID_SUBJECT=mailto:<an address that can be contacted about this server>")
        self.stderr.write("Changing these keys later disconnects every device; users must turn push on again.")
