"""Canonical email identities without accent-insensitive mailbox matching."""

import unicodedata

import idna


def canonical_email(value):
    if not isinstance(value, str):
        raise ValueError('Invalid email address')
    value = unicodedata.normalize('NFKC', value.strip()).lower()
    if value.count('@') != 1 or any(c.isspace() or unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('Invalid email address')
    local, domain = value.rsplit('@', 1)
    if not local or len(local) > 64 or not domain or domain.endswith('.'):
        raise ValueError('Invalid email address')
    try:
        domain = idna.encode(domain, uts46=True, transitional=False, std3_rules=True).decode('ascii')
    except idna.IDNAError as exc:
        raise ValueError('Invalid email domain') from exc
    result = local + '@' + domain
    if len(result) > 191 or '.' not in domain:
        raise ValueError('Invalid email address')
    return result


def email_lookup_candidates(value):
    canonical = canonical_email(value)
    local, domain = canonical.rsplit('@', 1)
    return canonical, local + '@' + idna.decode(domain)
