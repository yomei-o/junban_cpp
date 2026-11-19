#!/usr/bin/env python3
#
# 順番待ち API の検査。SQLite で動かしているサーバ（Python 版でも C++ 版でも）に同じ手順を流し、
# 応答を正規化して 1 行ずつ出す。両方の出力を diff すれば機能パリティを確かめられる。
#
#   python tools/junban_check.py http://localhost:8091 path/to/test_db.db > out_py.txt
#   python tools/junban_check.py http://localhost:8092 path/to/test_db.db > out_cpp.txt
#   diff out_py.txt out_cpp.txt
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

GUID = "0123456789abcdef0123456789abcdef"
SKEY = {"owner": "a" * 32, "staff": "b" * 32, "stranger": "c" * 32}


def seed():
    con = sqlite3.connect(DB)
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    t = now.strftime("%Y-%m-%d %H:%M:%S")
    exp = (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    for u in SKEY:
        con.execute("INSERT INTO account (user, status, role, created_at) VALUES (?, 'approved', 'member', ?)", (u, t))
        con.execute("INSERT INTO session (user, skey, expire, created_at) VALUES (?, ?, ?, ?)", (u, SKEY[u], exp, t))
    con.execute("INSERT INTO tenant (guid, name, created_by, created_at) VALUES (?, 'テスト食堂', 'owner', ?)", (GUID, t))
    tid = con.execute("SELECT id FROM tenant WHERE guid = ?", (GUID,)).fetchone()[0]
    con.execute("INSERT INTO tenant_member (tenant_id, user, status, role, created_at) "
                "VALUES (?, 'owner', 'approved', 'admin', ?)", (tid, t))
    con.execute("INSERT INTO tenant_member (tenant_id, user, status, role, created_at) "
                "VALUES (?, 'staff', 'approved', 'member', ?)", (tid, t))
    con.commit()
    con.close()


def call(path, body, who=None):
    h = {"Content-Type": "application/json"}
    if who:
        h["Authorization"] = "Bearer " + SKEY[who]
    req = urllib.request.Request(BASE + "/api/junban/v0/" + path, data=json.dumps(body).encode(),
                                 headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


# token・時刻・営業日・seat の id は実行ごとに変わるので伏せる（seat は名前で区別できる）
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


main()
