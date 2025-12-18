# export_csv.py
# Sprint 1: MOVE ONLY (CSV file writing helpers)

import os
import csv


def _ensure_dir(path, logger):
    if not path:
        return False
    try:
        if not os.path.isdir(path):
            os.makedirs(path)
        return True
    except Exception as ex:
        logger.warn("Export: could not create directory '{0}': {1}".format(path, ex))
        return False


def _append_csv_rows(path, headers, rows, logger):
    if not rows:
        return

    write_header = not os.path.exists(path) or os.path.getsize(path) == 0

    try:
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(headers)
            for r in rows:
                writer.writerow(r)
        logger.info("Export: appended {0} row(s) to '{1}'".format(len(rows), path))
    except Exception as ex:
        logger.warn("Export: failed to append to CSV '{0}': {1}".format(path, ex))
