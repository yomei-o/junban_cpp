#!/usr/bin/env python3
#
# 順番待ち API の検査。SQLite で動かしているサーバ（Python 版でも C++ 版でも）に同じ手順を流し、
# 応答を正規化して 1 行ずつ出す。両方の出力を diff すれば機能パリティを確かめられる。
#
#   python tools/junban_check.py http://localhost:8091 path/to/test_db.db > out_py.txt
#   python tools/junban_check.py http://localhost:8092 path/to/test_db.db > out_cpp.txt
#   diff out_py.txt out_cpp.txt
#
# TiDB(MySQL) のサーバを検査するときは DB に "mysql" を渡す（接続は env RDS_PROXY_HOST / TIDB_PORT /
# USER_NAME / PASSWORD / DB_NAME、要 pymysql）。店の guid を実行ごとに変え、最後に検査の行を全部消す。
#   python tools/junban_check.py https://junban-python.onrender.com mysql [https://junban-cpp.onrender.com]
# 3 つ目の URL を渡すと、2 つのサーバに同時に受付を投げて番号が重ならないかも見る。
#
# パスキーでのログインは自動化できないので、アカウント・セッション・テナント・メンバーを
# DB に直接入れる（サーバを起動してテーブルができた後で、空の DB に対して実行する）。
#
import concurrent.futures
import datetime
import json
import sqlite3
import sys
import urllib.request

BASE = sys.argv[1].rstrip("/")
DB = sys.argv[2]
OTHER = sys.argv[3].rstrip("/") if len(sys.argv) > 3 else ""
MYSQL = DB == "mysql"

if MYSQL:
    import os
    import secrets
    import ssl
    import pymysql
    RUN = secrets.token_hex(4)
    GUID = "c0ffee" + RUN + "0" * 18
    SKEY = {u: secrets.token_hex(16) for u in ("owner", "staff", "stranger")}
    USERS = {u: "junban_check_" + RUN + "_" + u for u in SKEY}
else:
    GUID = "0123456789abcdef0123456789abcdef"
    SKEY = {"owner": "a" * 32, "staff": "b" * 32, "stranger": "c" * 32}
    USERS = {u: u for u in SKEY}


class MyCon:
    """sqlite3 と同じ書き方（? の placeholder）で pymysql を使う"""
    def __init__(self):
        self.c = pymysql.connect(host=os.environ["RDS_PROXY_HOST"], port=int(os.environ.get("TIDB_PORT", "4000")),
                                 user=os.environ["USER_NAME"], password=os.environ["PASSWORD"],
                                 database=os.environ["DB_NAME"], ssl=ssl.create_default_context())

    def execute(self, sql, args=()):
        cur = self.c.cursor()
        cur.execute(sql.replace("?", "%s"), args)
        return cur

    def commit(self):
        self.c.commit()

    def close(self):
        self.c.close()


def connect():
    return MyCon() if MYSQL else sqlite3.connect(DB)


# 検査で入れた行を消す（TiDB のとき）
def cleanup():
    con = connect()
    row = con.execute("SELECT id FROM tenant WHERE guid = ?", (GUID,)).fetchone()
    if row:
        tid = row[0]
        for t in ("junban_ticket", "junban_seat", "tenant_member"):
            con.execute("DELETE FROM " + t + " WHERE tenant_id = ?", (tid,))
        con.execute("DELETE FROM tenant WHERE id = ?", (tid,))
    con.execute("DELETE FROM switch_state WHERE name = ?", ("junban/" + GUID,))
    for u in USERS.values():
        con.execute("DELETE FROM session WHERE user = ?", (u,))
        con.execute("DELETE FROM account WHERE user = ?", (u,))
    con.commit()
    con.close()


def seed():
    con = connect()
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    t = now.strftime("%Y-%m-%d %H:%M:%S")
    exp = (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    for u in SKEY:
        con.execute("INSERT INTO account (user, status, role, created_at) VALUES (?, 'approved', 'member', ?)",
                    (USERS[u], t))
        con.execute("INSERT INTO session (user, skey, expire, created_at) VALUES (?, ?, ?, ?)",
                    (USERS[u], SKEY[u], exp, t))
    con.execute("INSERT INTO tenant (guid, name, created_by, created_at) VALUES (?, 'テスト食堂', ?, ?)",
                (GUID, USERS["owner"], t))
    tid = con.execute("SELECT id FROM tenant WHERE guid = ?", (GUID,)).fetchone()[0]
    con.execute("INSERT INTO tenant_member (tenant_id, user, status, role, created_at) "
                "VALUES (?, ?, 'approved', 'admin', ?)", (tid, USERS["owner"], t))
    con.execute("INSERT INTO tenant_member (tenant_id, user, status, role, created_at) "
                "VALUES (?, ?, 'approved', 'member', ?)", (tid, USERS["staff"], t))
    con.commit()
    con.close()


def call(path, body, who=None, base=None):
    h = {"Content-Type": "application/json"}
    if who:
        h["Authorization"] = "Bearer " + SKEY[who]
    req = urllib.request.Request((base or BASE) + "/api/junban/v0/" + path, data=json.dumps(body).encode(),
                                 headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


# token・時刻・営業日・店の guid は実行ごとに変わるので伏せる。
# seat の id は TiDB だと実行ごとに違うので、出てきた順に S1, S2 … に置き換える（0 は「指定なし」のまま）
SEATMAP = {}


def seat_label(v):
    if not isinstance(v, int) or isinstance(v, bool) or v == 0:
        return v
    if v not in SEATMAP:
        SEATMAP[v] = "S" + str(len(SEATMAP) + 1)
    return SEATMAP[v]


def norm(o):
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if k == "token" and isinstance(v, str):
                v = "<token>"
            elif k.endswith("_at") and v is not None:
                v = "<time>"
            elif k == "biz_day":
                v = "<day>"
            elif k == "tenant" and v == GUID:
                v = "<tenant>"
            elif k == "seat" and isinstance(v, int):
                v = seat_label(v)
            else:
                v = norm(v)
            out[k] = v
        return out
    if isinstance(o, list):
        return [norm(x) for x in o]
    return o


def step(label, path, body, who=None):
    r = call(path, body, who)
    print(label + " " + path + " " + json.dumps(norm(r), ensure_ascii=False, sort_keys=True))
    return r


def main():
    seed()
    T = GUID
    step("01", "store", {"tenant": T})
    step("02", "store", {"tenant": "nope"})
    step("03", "take", {"tenant": T, "seat": 1, "people": 2})
    r1 = step("04", "take", {"tenant": T, "seat": 0, "people": 2})
    step("05", "take", {"tenant": T, "seat": 0, "people": 0})
    step("06", "take", {"tenant": T, "seat": 0, "people": True})
    step("07", "take", {"tenant": T, "seat": 0, "people": 100})
    step("08", "take", {"tenant": T, "people": 1})

    step("10", "seat_add", {"tenant": T, "name": "カウンター"}, "staff")
    step("11", "seat_add", {"tenant": T, "name": "カウンター"})
    c = step("12", "seat_add", {"tenant": T, "name": "カウンター"}, "owner")["seat"]["seat"]
    b = step("13", "seat_add", {"tenant": T, "name": "テーブル"}, "owner")["seat"]["seat"]
    global b2
    b2 = b
    step("14", "seat_add", {"tenant": T, "name": "テーブル"}, "owner")
    step("15", "seat_add", {"tenant": T, "name": ""}, "owner")

    step("20", "take", {"tenant": T, "seat": 0, "people": 1})
    step("21", "take", {"tenant": T, "seat": c, "people": 1})
    step("22", "take", {"tenant": T, "seat": b, "people": 4})
    r4 = step("23", "take", {"tenant": T, "seat": c, "people": 2})
    step("24", "ticket", {"token": r4["token"]})
    step("25", "store", {"tenant": T})
    step("26", "ticket", {"token": "nope"})
    step("27", "ticket", {})

    step("30", "call", {"tenant": T, "seat": c}, "stranger")
    step("31", "call", {"tenant": T, "seat": c})
    step("32", "call", {"tenant": T, "seat": c}, "staff")
    step("33", "ticket", {"token": r4["token"]})
    step("34", "call", {"tenant": T, "seat": b}, "staff")
    step("35", "call", {"tenant": T, "seat": b}, "staff")
    step("36", "call", {"tenant": T, "seat": "x"}, "staff")
    step("37", "store", {"tenant": T})

    step("40", "set_status", {"tenant": T, "number": 2, "status": "seated"}, "staff")
    step("41", "set_status", {"tenant": T, "number": 3, "status": "skipped"}, "staff")
    step("42", "set_status", {"tenant": T, "number": 3, "status": "waiting"}, "staff")
    step("43", "set_status", {"tenant": T, "number": 99, "status": "seated"}, "staff")
    step("44", "set_status", {"tenant": T, "number": 3, "status": "canceled"}, "staff")
    step("45", "set_status", {"tenant": T, "number": 3, "status": "called"}, "staff")

    step("50", "cancel", {"token": r4["token"]})
    step("51", "cancel", {"token": r4["token"]})
    step("52", "ticket", {"token": r4["token"]})

    step("60", "accepting", {"tenant": T, "on": False}, "staff")
    step("61", "take", {"tenant": T, "seat": c, "people": 1})
    step("62", "store", {"tenant": T})
    step("63", "accepting", {"tenant": T, "on": "yes"}, "staff")
    step("64", "accepting", {"tenant": T, "on": True}, "staff")

    step("70", "seat_rename", {"tenant": T, "seat": b, "name": "テーブル席"}, "owner")
    step("71", "seat_rename", {"tenant": T, "seat": b, "name": "カウンター"}, "owner")
    step("72", "seat_rename", {"tenant": T, "seat": b, "name": "x"}, "staff")
    step("73", "seat_delete", {"tenant": T, "seat": c}, "owner")
    step("74", "seat_delete", {"tenant": T, "seat": c}, "owner")
    step("75", "store", {"tenant": T})
    step("76", "ticket", {"token": r1["token"]})
    step("77", "queue", {"tenant": T}, "stranger")
    step("78", "queue", {"tenant": T}, "staff")

    # 同時に 12 人が受付しても番号は重ならず、抜けもない
    with concurrent.futures.ThreadPoolExecutor(12) as ex:
        rs = list(ex.map(lambda i: call("take", {"tenant": T, "seat": b, "people": 1}), range(12)))
    nums = sorted(r["ticket"]["number"] for r in rs if r.get("status") == "success")
    fails = [r.get("message") for r in rs if r.get("status") != "success"]
    print("80 concurrent take numbers=" + json.dumps(nums) + " fails=" + json.dumps(fails))

    # 2 つのサーバ（Python 版と C++ 版）に同時に受付しても、同じ DB なら番号は重ならない
    if OTHER:
        bases = [BASE, OTHER] * 6
        with concurrent.futures.ThreadPoolExecutor(12) as ex:
            rs = list(ex.map(lambda b: call("take", {"tenant": T, "seat": b2, "people": 1}, base=b), bases))
        nums = sorted(r["ticket"]["number"] for r in rs if r.get("status") == "success")
        fails = [r.get("message") for r in rs if r.get("status") != "success"]
        print("81 concurrent take on both servers numbers=" + json.dumps(nums) + " fails=" + json.dumps(fails))


if MYSQL:
    try:
        main()
    finally:
        cleanup()
else:
    main()
