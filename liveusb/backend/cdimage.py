"""Port of .hidden/scripts/cdimage: dump a physical CD/DVD to an ISO file."""

import hashlib
import os
import shutil
import subprocess

from . import run
from .. import messages


def run_cdimage(ctx):
    isofile_dir = os.path.join(ctx.work_dir, "ISOFILE")
    if os.path.isdir(isofile_dir):
        messages.extra_info("Purging", isofile_dir)
        shutil.rmtree(isofile_dir, ignore_errors=True)

    messages.info("Creating work directory")
    try:
        os.makedirs(isofile_dir, exist_ok=True)
    except OSError:
        messages.error("Unable to create work directory!")

    messages.info("Retrieving disk volume label")
    if run(["dd", "if=/dev/cdrom", "bs=1", "skip=32808", "count=32"]).returncode != 0:
        messages.error("Failed to read /dev/cdrom: No medium found!")

    messages.info("Extracting image file")
    iso_path = os.path.join(isofile_dir, "cdimage.iso")
    dd_proc = subprocess.Popen(["dd", "if=/dev/cdrom"], stdout=subprocess.PIPE)
    pv_proc = subprocess.Popen(["pv"], stdin=dd_proc.stdout, stdout=subprocess.PIPE)
    dd_proc.stdout.close()
    with open(iso_path, "wb") as out:
        shutil.copyfileobj(pv_proc.stdout, out)
    pv_proc.wait()
    if dd_proc.wait() != 0 or pv_proc.returncode != 0:
        messages.error("Unable to extract image file!")

    if not os.path.exists(iso_path):
        messages.error("The image file does not exist!")

    messages.info("Checking MD5Sum of disk and image file")
    md5cd = _md5_of_device("/dev/cdrom")
    md5iso = _md5_of_file(iso_path)

    messages.extra_info("Disk MD5 is:", md5cd)
    messages.extra_info("Image file MD5 is:", md5iso)
    messages.extra_info("Image file successfully extracted to:", iso_path)

    messages.info("Creating MD5Sum")
    md5sum_path = os.path.join(isofile_dir, "md5sum.txt")
    with open(md5sum_path, "w") as out:
        for root, _dirs, files in os.walk(isofile_dir):
            for name in files:
                if name == "md5sum.txt":
                    continue
                path = os.path.join(root, name)
                out.write(f"{_md5_of_file(path)}  {path}\n")
    os.chmod(iso_path, 0o555)


def _md5_of_file(path):
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _md5_of_device(path):
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
