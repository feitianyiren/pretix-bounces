import email
from email.utils import getaddresses
from imaplib import IMAP4_SSL

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.dispatch import receiver
from django.utils.translation import ugettext_lazy as _

from pretix.base.signals import email_filter, periodic_task, logentry_display
from .models import MailAlias
from .utils import generate_new_alias, get_content


@receiver(email_filter, dispatch_uid="pretix_bounces_email_filter")
def add_bounce_sender(sender, message: EmailMultiAlternatives, order, **kwargs):
    if not settings.CONFIG_FILE.has_section('bounces') or not order:
        return message

    if order.event.settings.smtp_use_custom:
        return message

    from_domain = settings.CONFIG_FILE.get('bounces', 'from_domain', fallback='')
    if from_domain and '@' + from_domain not in message.from_email:
        return message

    alias = generate_new_alias(order)
    from_email = message.from_email

    if 'Reply-To' not in message.extra_headers:
        message.extra_headers['Reply-To'] = from_email

    message.from_email = alias
    message.extra_headers.update({
        'From': from_email,
        'Sender': alias
    })

    return message


@receiver(periodic_task, dispatch_uid="pretix_bounces_periodic")
def get_bounces_via_imap(sender, **kwargs):
    host = settings.CONFIG_FILE.get('bounces', 'server', fallback='localhost').split(":")[0]
    try:
        port = settings.CONFIG_FILE.get('bounces', 'server', fallback='localhost').split(":")[1]
    except IndexError:
        port = 143
    imap = IMAP4_SSL(host, port)
    imap.login(
        settings.CONFIG_FILE.get('bounces', 'user', fallback='pretix'),
        settings.CONFIG_FILE.get('bounces', 'pass', fallback='')
    )
    imap.select()
    typ, data = imap.search(None, 'UnSeen')
    for num in data[0].split():
        typ, data = imap.fetch(num, '(RFC822)')
        msg = email.message_from_bytes(data[0][1])
        imap.store(num, '+FLAGS', '\\Seen')
        to = getaddresses(msg.get_all('To'))
        for name, addr in to:
            try:
                alias = MailAlias.objects.get(sender=addr)
            except MailAlias.DoesNotExist:
                continue
            content = get_content(msg)
            if isinstance(content, bytes):
                content = content.decode(errors='replace')
            alias.order.log_action(
                'pretix_bounces.order.email.received',
                data={
                    'subject': msg['Subject'],
                    'message': content,
                    'sender': msg['Sender'],
                    'full_mail': data[0][1].decode()
                }
            )
    imap.close()
    imap.logout()


@receiver(signal=logentry_display, dispatch_uid="pretix_bounces_logentry_display")
def pretixcontrol_logentry_display(sender, logentry, **kwargs):
    event_type = logentry.action_type
    plains = {
        'pretix_bounces.order.email.received': _('An email reply has been received by the user.'),
    }

    if event_type in plains:
        return plains[event_type]
