"""Local admin UI for managing providers, accounts, models, and endpoints
without hand-editing config.yaml / .env.

Every write in this package goes through ConfigWriter, which validates the
whole config with the same Pydantic model ConfigManager uses at startup
before anything touches disk, and backs up the previous file first.

This UI is intentionally unauthenticated, matching the rest of this
project's "trusted local network" operating assumption. It should not be
exposed beyond localhost/a trusted network.
"""
