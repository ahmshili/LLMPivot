"""Console-script entrypoint: `ai-gateway [options]`.

Runs the same FastAPI app as `uvicorn ai_gateway.main:app`, but with real
CLI flags instead of environment variables -- most usefully `--data-dir`,
for pointing the whole app (config.yaml, .env, everything) at a folder
outside the project checkout, e.g. a sibling directory shared with your
LiteLLM deployment:

    ai-gateway --data-dir ../data --port 8000

Every flag here just sets the same environment variable main.py already
reads (AI_GATEWAY_DATA_DIR, AI_GATEWAY_CONFIG, AI_GATEWAY_ENV_FILE,
AI_GATEWAY_LOG_LEVEL, AI_GATEWAY_PROD_MODE, AI_GATEWAY_ENABLE_ADMIN) --
this file adds no new configuration surface of its own, just a
friendlier way to set the existing one.
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ai-gateway",
        description="Run LLMPivot (equivalent to `uvicorn ai_gateway.main:app`, with friendlier flags).",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help=(
            "Directory containing config.yaml and .env (sets AI_GATEWAY_DATA_DIR). "
            "Lets the app's data live outside the project checkout, e.g. a sibling folder."
        ),
    )
    parser.add_argument("--config", default=None, help="Explicit path to config.yaml (overrides --data-dir for this file only).")
    parser.add_argument("--env-file", default=None, help="Explicit path to .env (overrides --data-dir for this file only).")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000).")
    parser.add_argument("--log-level", default=None, help="Log level, e.g. INFO, DEBUG (sets AI_GATEWAY_LOG_LEVEL).")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload (development only).")
    parser.add_argument(
        "--prod",
        action="store_true",
        help=(
            "Enable prod mode: /v1/* requires the configured API bearer token if one is set "
            "(security.api_auth_token_env), and the admin UI is disabled entirely unless "
            "--enable-admin is also passed. Local/trusted-network usage without this flag is "
            "completely unaffected -- everything stays exactly as unauthenticated as before."
        ),
    )
    parser.add_argument(
        "--enable-admin",
        action="store_true",
        help=(
            "Only meaningful together with --prod: re-enables the admin UI, gated behind its "
            "own bearer token (security.admin_auth_token_env). Startup fails if that token "
            "isn't configured with a real value -- there's no 'intentionally open' admin UI."
        ),
    )
    args = parser.parse_args()

    if args.data_dir:
        os.environ["AI_GATEWAY_DATA_DIR"] = args.data_dir
    if args.config:
        os.environ["AI_GATEWAY_CONFIG"] = args.config
    if args.env_file:
        os.environ["AI_GATEWAY_ENV_FILE"] = args.env_file
    if args.log_level:
        os.environ["AI_GATEWAY_LOG_LEVEL"] = args.log_level
    if args.prod:
        os.environ["AI_GATEWAY_PROD_MODE"] = "true"
    if args.enable_admin:
        os.environ["AI_GATEWAY_ENABLE_ADMIN"] = "true"

    # Unlike `uv run --env-file ../.env` (which loads the file before this
    # process even starts), running via this console script directly needs
    # to load it explicitly -- otherwise account secrets would never make
    # it into os.environ, even though AI_GATEWAY_ENV_FILE correctly points
    # the admin UI at the right file for *writing* new ones.
    from dotenv import load_dotenv

    from ai_gateway.main import _resolve_data_paths

    _, env_file_path = _resolve_data_paths()
    if env_file_path.exists():
        load_dotenv(env_file_path)

    import uvicorn

    uvicorn.run("ai_gateway.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
