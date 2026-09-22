"""Turns an account label into a short, recognizable account key -- used as
both the config.yaml account key and, combined with the provider name (via
the existing default_env_var_name), the generated environment variable
name. Never random.
"""

from __future__ import annotations

import re


def slugify_account_label(label: str) -> str:
    """'one.anon.201@gmail.com' -> 'one_anon_201'.

    The domain is dropped deliberately: the provider already identifies
    the service, so keeping the domain just makes an already-long env var
    name longer without adding information.
    """
    local_part = label.split("@", 1)[0] if "@" in label else label
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", local_part).strip("_").lower()
    return slug or "account"


def sanitize_account_key(label: str) -> str:
    """For an explicitly-typed label override: make it safe as a YAML key
    / URL path segment without domain-stripping or other assumptions --
    the admin typed this on purpose, so keep as much of it as possible.
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", label).strip("_").lower()
    return slug or "account"


def unique_account_key(existing_keys, base_slug: str) -> str:
    """Disambiguate a slug against a provider's existing account keys by
    appending _2, _3, ... -- still fully recognizable, never random.
    """
    if base_slug not in existing_keys:
        return base_slug
    suffix = 2
    while f"{base_slug}_{suffix}" in existing_keys:
        suffix += 1
    return f"{base_slug}_{suffix}"
