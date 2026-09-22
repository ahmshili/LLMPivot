from __future__ import annotations

from ai_gateway.admin.slugify import slugify_account_label, unique_account_key


def test_slugify_strips_domain() -> None:
    assert slugify_account_label("one.anon.201@gmail.com") == "one_anon_201"


def test_slugify_plain_label() -> None:
    assert slugify_account_label("backup account") == "backup_account"


def test_slugify_empty_falls_back() -> None:
    assert slugify_account_label("") == "account"
    assert slugify_account_label("@@@") == "account"


def test_slugify_lowercases() -> None:
    assert slugify_account_label("John.Doe@Example.com") == "john_doe"


def test_unique_account_key_no_collision() -> None:
    assert unique_account_key({}, "primary") == "primary"
    assert unique_account_key({"other": None}, "primary") == "primary"


def test_unique_account_key_collision_appends_suffix() -> None:
    existing = {"primary": None}
    assert unique_account_key(existing, "primary") == "primary_2"


def test_unique_account_key_multiple_collisions() -> None:
    existing = {"primary": None, "primary_2": None, "primary_3": None}
    assert unique_account_key(existing, "primary") == "primary_4"
