#!/usr/bin/env python3
"""
BIMS — провижининг устройств (серверный CLI).

Пишет в справочник bims_device_registry напрямую: без сетевого эндпойнта и без
device-токена. Запускается тем, кто провижионит ТСД, на сервере/в защищённом окружении.

Установка:
    pip install "psycopg[binary]>=3.1"

Креды БД — из окружения (не в аргументах, не в коде):
    export DATABASE_URL="postgresql://bims:***@localhost:5432/bims"
    # либо стандартные PG-переменные: PGHOST, PGUSER, PGPASSWORD, PGDATABASE

Примеры:
    python register_device.py register --serial DT40-0007 --clinic CLN001 --lab LAB001
    python register_device.py block   --serial DT40-0007
    python register_device.py unblock --serial DT40-0007
    python register_device.py list

ВНИМАНИЕ: имена колонок выверьте по финальному DDL (задача Sprint 1) —
здесь они по структуре из ТЗ §7.2.
"""
import argparse
import os
import re
import sys

import psycopg

SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{4,64}$")


def connect():
    dsn = os.environ.get("DATABASE_URL")
    try:
        return psycopg.connect(dsn) if dsn else psycopg.connect()
    except psycopg.Error as exc:
        sys.exit(f"[device-admin] не удалось подключиться к БД: {exc}")


def _check_serial(serial: str) -> None:
    if not SERIAL_RE.match(serial):
        sys.exit(f"[device-admin] неверный серийник '{serial}': ожидается 4–64 символа [A-Za-z0-9._:-]")


def cmd_register(conn, args):
    _check_serial(args.serial)
    with conn.cursor() as cur:
        # FK-страховка: конфигурация лаборатории должна существовать
        cur.execute("SELECT 1 FROM dict_labs_configs WHERE lab_code = %s", (args.lab,))
        if cur.fetchone() is None:
            sys.exit(f"[device-admin] лаборатория '{args.lab}' не найдена в dict_labs_configs — сначала заведите её")
        cur.execute(
            """INSERT INTO bims_device_registry (device_serial, clinic_code, lab_code, status)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (device_serial)
               DO UPDATE SET clinic_code = EXCLUDED.clinic_code,
                             lab_code   = EXCLUDED.lab_code,
                             status     = EXCLUDED.status""",
            (args.serial, args.clinic, args.lab, args.status),
        )
    conn.commit()
    print(f"[device-admin] устройство '{args.serial}' → {args.clinic}/{args.lab}, статус: {args.status}")


def _set_status(conn, serial: str, status: str):
    _check_serial(serial)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE bims_device_registry SET status = %s WHERE device_serial = %s",
            (status, serial),
        )
        if cur.rowcount == 0:
            sys.exit(f"[device-admin] устройство '{serial}' не зарегистрировано")
    conn.commit()
    print(f"[device-admin] устройство '{serial}' → статус: {status}")


def cmd_block(conn, args):
    _set_status(conn, args.serial, "blocked")


def cmd_unblock(conn, args):
    _set_status(conn, args.serial, "active")


def cmd_list(conn, args):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT device_serial, clinic_code, lab_code, status "
            "FROM bims_device_registry ORDER BY device_serial"
        )
        rows = cur.fetchall()
    if not rows:
        print("[device-admin] устройств не зарегистрировано")
        return
    print(f"{'serial':<24} {'clinic':<10} {'lab':<10} status")
    for serial, clinic, lab, status in rows:
        print(f"{serial:<24} {clinic:<10} {lab:<10} {status}")


def main():
    ap = argparse.ArgumentParser(description="BIMS device provisioning CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_reg = sub.add_parser("register", help="зарегистрировать/обновить устройство")
    p_reg.add_argument("--serial", required=True)
    p_reg.add_argument("--clinic", required=True)
    p_reg.add_argument("--lab", required=True)
    p_reg.add_argument("--status", default="active", choices=["active", "blocked"])
    p_reg.set_defaults(func=cmd_register)

    p_block = sub.add_parser("block", help="заблокировать устройство")
    p_block.add_argument("--serial", required=True)
    p_block.set_defaults(func=cmd_block)

    p_unblock = sub.add_parser("unblock", help="разблокировать устройство")
    p_unblock.add_argument("--serial", required=True)
    p_unblock.set_defaults(func=cmd_unblock)

    p_list = sub.add_parser("list", help="показать все устройства")
    p_list.set_defaults(func=cmd_list)

    args = ap.parse_args()
    conn = connect()
    try:
        args.func(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
