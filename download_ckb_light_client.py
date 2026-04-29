"""
download.py

This module downloads files from specified URLs and saves them locally.
"""

import os
import platform
import tarfile
import zipfile
import requests
from tqdm import tqdm

versions = [
    "0.5.0",
    "0.5.3",
]  # Replace with your versions

DOWNLOAD_DIR = "download"
SYSTEMS = {
    "Windows": {
        "url": "https://github.com/nervosnetwork/ckb-light-client/releases/download/v{version}/ckb-light-client_v{"
        "version}-x86_64-windows.tar.gz",
        "ext": ".tar.gz",
    },
    "Linux": {
        "x86_64": {
            "url": "https://github.com/nervosnetwork/ckb-light-client/releases/download/v{version}/ckb-light-client_v{"
            "version}-x86_64-linux-portable.tar.gz",
            "ext": ".tar.gz",
        },
    },
    "Darwin": {
        "x86_64": {
            "url": "https://github.com/nervosnetwork/ckb-light-client/releases/download/v{version}/ckb-light-client_v{"
            "version}-x86_64-darwin-portable.tar.gz",
            "ext": ".tar.gz",
        },
        "arm64": {
            "url": "https://github.com/nervosnetwork/ckb-light-client/releases/download/v{version}/ckb-light-client_v{"
            "version}-x86_64-darwin.tar.gz",
            "ext": ".tar.gz",
        },
    },
}


def download_file(url, filename):
    """
    Download a file from the specified URL and save it locally.

    Args:
        url (str): The URL of the file to download.
        filename (str): The name to save the downloaded file as.

    Raises:
        requests.HTTPError: If an HTTP error occurs during the download.

    """
    print(f"Downloading URL: {url}")
    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()

    total_size = int(response.headers.get("content-length", 0))
    block_size = 1024  # 1 Kibibyte
    tq_file = tqdm(total=total_size, unit="iB", unit_scale=True)

    with open(filename, "wb") as file:
        for data in response.iter_content(block_size):
            tq_file.update(len(data))
            file.write(data)
    tq_file.close()

    if total_size not in (0, total_size):
        raise requests.HTTPError("ERROR: Something went wrong during the download.")


def extract_file(filename, path):
    """
    Extract a compressed file to the specified path.

    Args:
        filename (str): The name of the compressed file.
        path (str): The path to extract the files to.
    """
    temp_path = path
    os.makedirs(temp_path, exist_ok=True)

    if filename.endswith(".zip"):
        with zipfile.ZipFile(filename, "r") as zip_ref:
            zip_ref.extractall(temp_path)
    elif filename.endswith(".tar.gz"):
        with tarfile.open(filename, "r:gz") as tar_ref:
            tar_ref.extractall(temp_path)
    # Change permission of ckb-light-client
    for file in ["ckb-light-client"]:
        filepath = os.path.join(path, file)
        if os.path.isfile(filepath):
            os.chmod(filepath, 0o755)


def download_ckb(ckb_version):
    """
    download ckb from gitHub by ckb version
    :param ckb_version: gitHub release ckb version
    :return: None
    """
    system = platform.system()
    architecture = platform.machine() if system in ["Linux", "Darwin"] else ""
    print(
        f"system:{system},architecture:{architecture}".format(
            system=system, architecture=architecture
        )
    )
    url = SYSTEMS[system][architecture]["url"].format(version=ckb_version)
    ext = SYSTEMS[system][architecture]["ext"]

    filename = f"ckb_light_client_v{ckb_version}_binary{ext}"
    download_path = os.path.join(DOWNLOAD_DIR, ckb_version).split("-")[0]
    os.makedirs(download_path, exist_ok=True)

    download_file(url, filename)
    extract_file(filename, download_path)


for version in versions:
    download_ckb(version)
