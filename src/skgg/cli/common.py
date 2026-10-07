"""Command-line helpers shared by the `cli/` scripts that read a run config: the
`-f`/`--config-file` and `--log-level` options, and loading the config they
name. Not a script itself."""

import argparse

from skgg.core.config import RunConfig
from skgg.core.utils import resolve_config_path, setup_logging


def add_config_args(
    parser: argparse.ArgumentParser, required: bool = True, config_help: str = ""
) -> None:
    """Adds `-f`/`--config-file` and `--log-level` to `parser`. `config_help` is
    appended to the help text of `-f`."""
    config_file_help = (
        "Config file under configurations/ (e.g. fr.no-literals), or a path to "
        "one. The .json extension is optional."
    )
    parser.add_argument(
        "-f",
        "--config-file",
        required=required,
        default=None,
        help=f"{config_file_help} {config_help}".rstrip(),
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Logging level (e.g. DEBUG, INFO, WARNING), overriding the config "
        "file's logging.level.",
    )


def load_config(args: argparse.Namespace) -> RunConfig:
    """Loads the config named by `args.config_file` and sets up logging at
    `args.log_level`, or else at the config's `logging.level`."""
    config = RunConfig.from_json(resolve_config_path(args.config_file))
    setup_logging(
        level=args.log_level if args.log_level is not None else config.logging.level
    )
    return config
