#!/usr/bin/env python3
"""
export_emails.py — выгрузка email-адресов из локального SQLite по ICP-сегментам.

Читает analysis_results (ICP-метки) и accounts (email-адреса),
джойнит по username/login, записывает в txt-файлы.

Usage:
    python3 export_emails.py
    python3 export_emails.py --output-dir /path/to/dir
"""

import argparse
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sqlite_store import connect, export_icp_emails, get_db_path, init_db


def main():
    parser = argparse.ArgumentParser(description="Export emails by ICP segment")
    parser.add_argument("--output-dir", default=".", help="Directory for output txt files")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    conn = connect()
    try:
        init_db(conn)
        print(f"Чтение SQLite: {get_db_path()}")
        segments = export_icp_emails(conn)
    finally:
        conn.close()

    all_emails: set[str] = set().union(*segments.values()) if segments else set()

    print("\n" + "=" * 50)
    print("Результаты:")
    print("=" * 50)

    for icp in ["ICP1", "ICP2", "ICP3", "ICP4", "ICP5"]:
        emails = sorted(segments[icp])
        filename = f"{icp.lower()}_emails.txt"
        filepath = os.path.join(args.output_dir, filename)
        with open(filepath, "w") as f:
            f.write("\n".join(emails))
            if emails:
                f.write("\n")
        print(f"  {icp}: {len(emails):>6} emails  ->  {filepath}")

    all_sorted = sorted(all_emails)
    all_path = os.path.join(args.output_dir, "all_icp_emails.txt")
    with open(all_path, "w") as f:
        f.write("\n".join(all_sorted))
        if all_sorted:
            f.write("\n")
    print(f"  ALL:  {len(all_sorted):>6} emails  ->  {all_path}")
    print("=" * 50)
    print("Готово!")


if __name__ == "__main__":
    main()
