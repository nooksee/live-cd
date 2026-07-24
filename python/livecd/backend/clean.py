"""Port of .hidden/scripts/clean: purge the work directories."""

import os

from . import mounts
from .. import messages


def run_clean(ctx):
    if os.path.isdir(ctx.fs_dir) or os.path.isdir(ctx.iso_dir):
        mounts.purge_work_dirs(ctx)
    else:
        messages.info("There is nothing to clean!")
