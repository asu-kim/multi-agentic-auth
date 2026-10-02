"""Compatibility launcher; each agent also has its own package entry point."""
import argparse
from importlib import import_module

from capability_team.common.server_runtime import run_server


def main():
    parser = argparse.ArgumentParser(description="Run one agent's A2A server")
    parser.add_argument("role", choices=["manager", "language", "analytics", "robot"])
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    module = import_module(f"capability_team.{args.role}_agent.__main__")
    run_server(args.role, module.create_app, ["--host", args.host])


if __name__ == "__main__":
    main()
