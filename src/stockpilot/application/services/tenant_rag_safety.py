"""Bounded credential policy, independent of model instructions or responses.

This is not a universal detector of arbitrary secrets. It recognizes explicit
credential fields, common token formats and two bounded encoding layers.
"""
import base64
import re
import unicodedata
from urllib.parse import unquote

RAG_SENSITIVE_REFUSAL = 'Je ne peux pas divulguer de secrets, mots de passe ou jetons. Consultez la procédure autorisée de gestion des accès.'
RAG_CITATION_REFUSAL = 'La réponse documentaire contient une référence non vérifiable. Reformulez la question ou consultez les documents autorisés.'

_LABEL = r'(?:api[ _-]?key|cle[ _-]?(?:api|secrete|privee)|client[ _-]?secret|code secret(?: [a-z0-9_-]{1,32})?|mot de passe|password|passwd|secret|access[ _-]?token|refresh[ _-]?token|authorization|cookie)'
_ASSIGNMENT = re.compile(r'\b' + _LABEL + r'[\s"\x27]*[:=][\s"\x27]*(?:bearer\s+)?([^\s"\x27,;}<>]{4,})', re.I)
_TOKENS = re.compile(r'\b(?:sk_(?:live|test)_[A-Za-z0-9_-]{12,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b', re.I)
_PRIVATE_KEY = re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----', re.I)
_URI_PASSWORD = re.compile(r'\b[a-z][a-z0-9+.-]*://[^\s/:]+:([^\s/@]+)@', re.I)
_EXTRACTION = re.compile(r'\b(?:donne|donner|affiche|afficher|montre|montrer|revele|reveler|divulgue|extraire|extract|reveal|show|give|print|dump)\b.{0,160}\b' + _LABEL, re.I)
_VALUE_QUESTION = re.compile(r'\b(?:quel(?:le)?s? (?:est|sont)|what (?:is|are))\s+(?:(?:le|la|les|mon|ma|the|my)\s+)?' + _LABEL, re.I)


def normalized(value):
    value = ''.join(c for c in unicodedata.normalize('NFKC', value) if unicodedata.category(c) != 'Cf')
    return ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c)).lower()


def variants(value):
    """Bounded decoding; no network, recursive parser, archive or model calls."""
    values = [value]
    for _ in range(2):
        decoded = unquote(values[-1])
        if decoded == values[-1]: break
        values.append(decoded)
    for candidate in re.findall(r'(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/])', value)[:32]:
        try:
            decoded = base64.b64decode(candidate, validate=True).decode('utf-8')
        except (ValueError, UnicodeError):
            continue
        if len(decoded) <= 64000: values.append(decoded)
    return values


def sensitive_values(value):
    found = set()
    for variant in variants(value):
        clean = normalized(variant)
        found.update(match[1] for match in _ASSIGNMENT.finditer(clean))
        found.update(match[1] for match in _URI_PASSWORD.finditer(clean))
        found.update(match[0] for match in _TOKENS.finditer(clean))
        if _PRIVATE_KEY.search(clean): found.add('-----begin private key-----')
    return frozenset(found)


def contains_sensitive(value):
    return bool(sensitive_values(value))


def requests_secret(question):
    value = normalized(question)
    value = re.sub(r'\bsecrets? de fabrication\b', 'techniques de fabrication', value)
    return contains_sensitive(question) or bool(_EXTRACTION.search(value) or _VALUE_QUESTION.search(value))


def output_is_safe(value, protected_values=()):
    if contains_sensitive(value): return False
    return not any(normalized(secret) in normalized(variant)
                   for variant in variants(value) for secret in protected_values if len(secret) >= 4)


def safe_public(value):
    """Screen public snapshots/metadata without mutating historical storage."""
    if isinstance(value, str): return not contains_sensitive(value)
    if isinstance(value, dict):
        # Inspect the association too: scanning keys and values independently
        # would miss {"password": "otherwise-unrecognizable-value"}.
        return all(safe_public(k) and safe_public(v) and not (
            isinstance(k, str) and isinstance(v, (str, int, float))
            and contains_sensitive(k + '=' + str(v))
        ) for k, v in value.items())
    if isinstance(value, (tuple, list)): return all(safe_public(v) for v in value)
    return True
