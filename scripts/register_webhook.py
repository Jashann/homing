"""Registers Homing's webhook with the PayPal sandbox and prints its id (never the credentials).

    uv run python scripts/register_webhook.py https://your-public-host.example.org
    export PAYPAL_WEBHOOK_ID=<the id it prints>      # then start the server

The URL must be public and https; /api/webhooks/paypal is added if it is not already there. The webhook listens
for the payout item, payout batch and invoice events (homing.paypal.WEBHOOK_EVENTS). If a webhook already exists for
that URL, its id is printed instead of making a second one. The server verifies every delivery with PayPal
(verify-webhook-signature) before applying it, and logs each verified one as "confirmed by PayPal webhook".
"""
from __future__ import annotations

import sys
from urllib.parse import urlparse

from homing.paypal import WEBHOOK_EVENTS, PayPalClient, PayPalError, credentials_available

PATH = "/api/webhooks/paypal"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    url = sys.argv[1].rstrip("/")
    parts = urlparse(url)
    if parts.scheme != "https" or not parts.netloc:
        sys.exit("PayPal only delivers webhooks to a public https URL, for example https://homing.example.org")
    if not parts.path.endswith(PATH):
        url += PATH
    if not credentials_available():
        sys.exit("No PayPal sandbox keys found (~/.paypal_sandbox, or PAYPAL_CLIENT_ID and PAYPAL_SECRET).")
    with PayPalClient() as client:
        try:
            hook = client.create_webhook(url, WEBHOOK_EVENTS)
        except PayPalError as e:
            sys.exit(f"PayPal refused the webhook: {e.status} {e.name}")
    print(f"webhook for {url}", file=sys.stderr)
    print(f"events: {', '.join(WEBHOOK_EVENTS)}", file=sys.stderr)
    print(hook["id"])
    print(f"export PAYPAL_WEBHOOK_ID={hook['id']}", file=sys.stderr)


if __name__ == "__main__":
    main()
