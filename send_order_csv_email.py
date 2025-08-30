"""Fetch a sales order CSV from InvenTree and send it by email.

Usage examples:
  # environment variables (recommended)
  set INVENTREE_URL=http://inventree.localhost
  set INVENTREE_API_TOKEN=inv-...
  set SMTP_HOST=smtp.gmail.com
  set SMTP_PORT=587
  set SMTP_USER=your_smtp_user
  set SMTP_PASSWORD=your_smtp_password
  set FROM_EMAIL=your@example.com

  python send_order_csv_email.py --order 87 --to spraveen2666@gmail.com

Notes:
- The script requires network access to the InvenTree server and a working SMTP account.
- For Gmail you may need an app password or allow less-secure apps (not recommended).
"""
import os
import argparse
import requests
import smtplib
from email.message import EmailMessage
from typing import Optional

INVENTREE_URL = os.getenv('INVENTREE_URL', 'http://inventree.localhost')
API_TOKEN = os.getenv('INVENTREE_API_TOKEN')
SMTP_HOST = os.getenv('SMTP_HOST')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
FROM_EMAIL = os.getenv('FROM_EMAIL', SMTP_USER)

HEADERS = {}
if API_TOKEN:
    HEADERS['Authorization'] = f'Token {API_TOKEN}'


def fetch_order_csv(order_index: int) -> bytes:
    """Download the CSV bytes for a given sales order index."""
    url = f"{INVENTREE_URL}/order/sales-order/{order_index}/export/"
    params = {'format': 'csv'}
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.content


def fetch_company_email(company_pk: int) -> Optional[str]:
    """Fetch company details from InvenTree API and return the email address if present."""
    url = f"{INVENTREE_URL}/api/company/{company_pk}/"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get('email')


def send_email_with_attachment(to_email: str, subject: str, body: str, attachment_bytes: bytes, filename: str, smtp_host: str, smtp_port: int, smtp_user: Optional[str], smtp_password: Optional[str], from_email: str):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email
    msg.set_content(body)
    msg.add_attachment(attachment_bytes, maintype='text', subtype='csv', filename=filename)

    # Connect and send
    if smtp_port == 465:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.starttls()
    try:
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.send_message(msg)
    finally:
        server.quit()


def main():
    parser = argparse.ArgumentParser(description='Fetch InvenTree sales order CSV and email it')
    parser.add_argument('--order', type=int, required=True, help='Sales order index (e.g. 87)')
    parser.add_argument('--to', required=True, help='Recipient email address')
    parser.add_argument('--subject', default=None, help='Email subject')
    parser.add_argument('--body', default='Please find attached the sales order CSV.', help='Email body')
    parser.add_argument('--smtp-host', default=None, help='SMTP host (overrides env)')
    parser.add_argument('--smtp-port', type=int, default=None, help='SMTP port (overrides env)')
    parser.add_argument('--smtp-user', default=None, help='SMTP username (overrides env)')
    parser.add_argument('--smtp-pass', default=None, help='SMTP password (overrides env)')
    parser.add_argument('--from-email', default=None, help='From address (overrides env)')

    args = parser.parse_args()

    smtp_host = args.smtp_host or SMTP_HOST
    smtp_port = args.smtp_port or SMTP_PORT
    smtp_user = args.smtp_user or SMTP_USER
    smtp_pass = args.smtp_pass or SMTP_PASSWORD
    from_email = args.from_email or FROM_EMAIL

    if not smtp_host:
        raise SystemExit('SMTP_HOST not set (env) or --smtp-host required')
    if not from_email:
        raise SystemExit('FROM_EMAIL not set and SMTP_USER not provided')

    try:
        csv_bytes = fetch_order_csv(args.order)
    except Exception as e:
        raise SystemExit(f'Failed to download order CSV: {e}')

    subject = args.subject or f'InvenTree Sales Order {args.order} (CSV)'
    filename = f'sales_order_{args.order}.csv'

    try:
        send_email_with_attachment(args.to, subject, args.body, csv_bytes, filename, smtp_host, smtp_port, smtp_user, smtp_pass, from_email)
    except Exception as e:
        raise SystemExit(f'Failed to send email: {e}')

    print(f'Email sent to {args.to} with attachment {filename}')


if __name__ == '__main__':
    main()
