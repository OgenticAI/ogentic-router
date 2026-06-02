"""Click CLI entry point — scaffold only.

Real subcommands (`serve`, `route`, `policies`) land in the v0.1 build.
This file exists so ``pip install -e .`` registers the
``ogentic-router`` console script and downstream packagers can wire
their entry points immediately.
"""

from __future__ import annotations

import click

from ogentic_router import __version__


@click.group()
@click.version_option(version=__version__, prog_name="ogentic-router")
def cli() -> None:
    """ogentic-router: Privacy-aware LLM routing.

    Pre-alpha. Real subcommands land with v0.1
    (see https://linear.app/ogenticai/project/ogentic-router-oss-46e612b52d27).
    """


if __name__ == "__main__":
    cli()
