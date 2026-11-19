#
# 順番待ち（整理券）
#
# 店 = テナント（guid で指定）。お客さんはログイン不要、店員はそのテナントの承認済みメンバー。
#
# お客さん（ログイン不要）
#   /api/junban/v0/store        {tenant} 店名、受付中か、席の種類ごとの待ち組数、呼出中の番号
#   /api/junban/v0/take         {tenant, seat, people} 整理券を取る（token を返す）
#   /api/junban/v0/ticket       {token} 自分の整理券と「あと何組」
#   /api/junban/v0/cancel       {token} 取り消す（待ち・呼出中のときだけ）
#
# 店員（そのテナントの承認済みメンバー）
#   /api/junban/v0/queue        {tenant} 今日の整理券ぜんぶと席の種類
#   /api/junban/v0/call         {tenant, seat} その席の列でいちばん前の人を呼ぶ
#   /api/junban/v0/set_status   {tenant, number, status} 状態を変える（waiting/called/seated/skipped）
#   /api/junban/v0/accepting    {tenant, on} 受付の開始・停止
#
# 店の管理者（そのテナントの管理者）
#   /api/junban/v0/seat_add     {tenant, name} 席の種類を足す
#   /api/junban/v0/seat_rename  {tenant, seat, name} 名前を変える
#   /api/junban/v0/seat_delete  {tenant, seat} 消す（非表示にするだけ）
#
# 番号は店ごと・営業日ごとに 1 から、席の種類をまたいだ通し番号。営業日は日本時間の 4 時で切り替える。
# 席の種類が 1 つもない店は seat = 0（指定なし）の 1 列だけ。
# C++ 版は cpp/main/testsv_api_junban.h。検査の順番と失敗時の message を揃えてある。
#
import datetime
import secrets

from fastapi import APIRouter, Request, Header, Depends
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import engine
import model_junban
import model_switch
import model_tenant
from testsv_api_passkey import (
    PasskeyError,
    arun,
    extract_bearer_token,
    format_time,
    get_db,
    read_body,
    session_user,
    utcnow,
    valid_user_name,
)
from testsv_api_tenant import (
    get_tenant,
    tenant_admin_of,
    tenant_member_of,
)

router = APIRouter()

Seat = model_junban.JunbanSeat
Ticket = model_junban.JunbanTicket

ST_WAITING = "waiting"
ST_CALLED = "called"
ST_SEATED = "seated"
ST_SKIPPED = "skipped"
ST_CANCELED = "canceled"

PEOPLE_MAX = 99
TAKE_RETRY = 5


def init_junban():
    print("init_junban()")
    model_junban.Base.metadata.create_all(bind=engine)

init_junban()


# 営業日（日本時間の 4 時で切り替え）= UTC + 9 時間 - 4 時間 の日付
def biz_day_of(t: datetime.datetime) -> int:
    d = t + datetime.timedelta(hours=5)
    return d.year * 10000 + d.month * 100 + d.day


def today() -> int:
    return biz_day_of(utcnow())


def accepting_name(guid: str) -> str:
    return "junban/" + guid


# 受付中か。まだ一度も切り替えていなければ受付中
def is_accepting(db: Session, guid: str) -> bool:
    s = db.query(model_switch.SwitchState).filter(
        model_switch.SwitchState.name == accepting_name(guid)).first()
    return s is None or s.value == 1


def int_of(body: dict, key: str):
    v = body.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v


def active_seats(db: Session, tenant_id: int) -> list:
    return db.query(Seat).filter(Seat.tenant_id == tenant_id, Seat.active == 1) \
        .order_by(Seat.sort_order, Seat.id).all()


def seat_names(db: Session, tenant_id: int) -> dict:
    return {s.id: s.name for s in db.query(Seat).filter(Seat.tenant_id == tenant_id)}


def get_active_seat(db: Session, tenant_id: int, body: dict):
    seat = int_of(body, "seat")
    if seat is None:
        raise PasskeyError("invalid request")
    s = db.query(Seat).filter(Seat.tenant_id == tenant_id, Seat.id == seat, Seat.active == 1).first()
    if s is None:
        raise PasskeyError("unknown seat")
    return s


def seat_to_dict(s) -> dict:
    return {"seat": s.id, "name": s.name, "active": s.active == 1}


def ticket_to_dict(t, names: dict) -> dict:
    return {
        "number": t.num,
        "seat": t.seat_id,
        "seat_name": names.get(t.seat_id),
        "people": t.people,
        "status": t.status,
        "created_at": format_time(t.created_at),
        "called_at": format_time(t.called_at),
        "done_at": format_time(t.done_at),
    }


def today_tickets(db: Session, tenant_id: int) -> list:
    return db.query(Ticket).filter(Ticket.tenant_id == tenant_id, Ticket.biz_day == today()) \
        .order_by(Ticket.num).all()


def get_ticket_by_token(db: Session, body: dict):
    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise PasskeyError("invalid request")
    t = db.query(Ticket).filter(Ticket.token == token).first()
    if t is None:
        raise PasskeyError("unknown ticket")
    return t


def get_today_ticket(db: Session, tenant_id: int, body: dict):
    num = int_of(body, "number")
    if num is None:
        raise PasskeyError("invalid request")
    t = db.query(Ticket).filter(Ticket.tenant_id == tenant_id, Ticket.biz_day == today(),
                                Ticket.num == num).first()
    if t is None:
        raise PasskeyError("unknown ticket")
    return t


# 店員の入口: ログインしていて、そのテナントの承認済みメンバー
def staff_of(db: Session, skey: str, body: dict):
    user = session_user(db, skey)
    t = get_tenant(db, body)
    m = tenant_member_of(db, t.id, user)
    return user, t, m


# 店の管理者の入口
def store_admin_of(db: Session, skey: str, body: dict):
    user = session_user(db, skey)
    t = get_tenant(db, body)
    tenant_admin_of(db, t.id, user)
    return user, t


#
# お客さん
#

def junban_store(db: Session, skey: str, body: dict) -> dict:
    t = get_tenant(db, body)
    tickets = today_tickets(db, t.id)
    seats = []
    for s in active_seats(db, t.id):
        seats.append({"seat": s.id, "name": s.name,
                      "waiting": sum(1 for x in tickets if x.seat_id == s.id and x.status == ST_WAITING)})
    return {
        "status": "success",
        "store": {
            "tenant": t.guid,
            "name": t.name,
            "accepting": is_accepting(db, t.guid),
            "seats": seats,
            "waiting": sum(1 for x in tickets if x.status == ST_WAITING),
            "calling": [x.num for x in tickets if x.status == ST_CALLED],
        },
    }


def junban_take(db: Session, skey: str, body: dict) -> dict:
    t = get_tenant(db, body)
    if not is_accepting(db, t.guid):
        raise PasskeyError("not accepting")
    people = int_of(body, "people")
    if people is None or people < 1 or people > PEOPLE_MAX:
        raise PasskeyError("invalid people")
    seat = int_of(body, "seat")
    if seat is None:
        raise PasskeyError("invalid request")
    seats = active_seats(db, t.id)
    if seats:
        if seat not in [s.id for s in seats]:
            raise PasskeyError("unknown seat")
    elif seat != 0:
        raise PasskeyError("unknown seat")
    token = secrets.token_hex(16)
    day = today()
    # 同時に受付した人と同じ番号になったら（一意制約で弾かれたら）振り直す
    for _ in range(TAKE_RETRY):
        top = db.query(func.max(Ticket.num)).filter(Ticket.tenant_id == t.id, Ticket.biz_day == day).scalar()
        x = Ticket(tenant_id=t.id, biz_day=day, num=(top or 0) + 1, seat_id=seat, people=people,
                   status=ST_WAITING, token=token, created_at=utcnow())
        db.add(x)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        return {"status": "success", "token": token, "ticket": ticket_to_dict(x, seat_names(db, t.id))}
    raise PasskeyError("busy")


def junban_ticket(db: Session, skey: str, body: dict) -> dict:
    x = get_ticket_by_token(db, body)
    t = db.query(model_tenant.Tenant).filter(model_tenant.Tenant.id == x.tenant_id).first()
    if t is None:
        raise PasskeyError("unknown ticket")
    d = ticket_to_dict(x, seat_names(db, t.id))
    is_today = x.biz_day == today()
    d["ahead"] = None
    calling = []
    if is_today:
        tickets = today_tickets(db, t.id)
        if x.status == ST_WAITING:
            d["ahead"] = sum(1 for y in tickets
                             if y.seat_id == x.seat_id and y.status == ST_WAITING and y.num < x.num)
        calling = [y.num for y in tickets if y.status == ST_CALLED]
    return {
        "status": "success",
        "ticket": d,
        "today": is_today,
        "store": {"tenant": t.guid, "name": t.name, "calling": calling},
    }


def junban_cancel(db: Session, skey: str, body: dict) -> dict:
    x = get_ticket_by_token(db, body)
    if x.status not in (ST_WAITING, ST_CALLED):
        raise PasskeyError("cannot cancel")
    x.status = ST_CANCELED
    x.done_at = utcnow()
    db.commit()
    return {"status": "success", "ticket": ticket_to_dict(x, seat_names(db, x.tenant_id))}


#
# 店員
#

def junban_queue(db: Session, skey: str, body: dict) -> dict:
    user, t, m = staff_of(db, skey, body)
    seats = db.query(Seat).filter(Seat.tenant_id == t.id).order_by(Seat.sort_order, Seat.id).all()
    names = {s.id: s.name for s in seats}
    return {
        "status": "success",
        "tenant": t.guid,
        "name": t.name,
        "my_role": m.role,
        "accepting": is_accepting(db, t.guid),
        "biz_day": today(),
        "seats": [seat_to_dict(s) for s in seats],
        "tickets": [ticket_to_dict(x, names) for x in today_tickets(db, t.id)],
    }


def junban_call(db: Session, skey: str, body: dict) -> dict:
    user, t, m = staff_of(db, skey, body)
    seat = int_of(body, "seat")
    if seat is None:
        raise PasskeyError("invalid request")
    x = db.query(Ticket).filter(Ticket.tenant_id == t.id, Ticket.biz_day == today(),
                                Ticket.seat_id == seat, Ticket.status == ST_WAITING) \
        .order_by(Ticket.num).first()
    if x is None:
        raise PasskeyError("no one waiting")
    x.status = ST_CALLED
    x.called_at = utcnow()
    db.commit()
    return {"status": "success", "ticket": ticket_to_dict(x, seat_names(db, t.id))}


def junban_set_status(db: Session, skey: str, body: dict) -> dict:
    user, t, m = staff_of(db, skey, body)
    x = get_today_ticket(db, t.id, body)
    status = body.get("status")
    if status not in (ST_WAITING, ST_CALLED, ST_SEATED, ST_SKIPPED):
        raise PasskeyError("invalid status")
    x.status = status
    if status == ST_WAITING:
        x.called_at = None
        x.done_at = None
    elif status == ST_CALLED:
        x.called_at = utcnow()
        x.done_at = None
    else:
        x.done_at = utcnow()
    db.commit()
    return {"status": "success", "ticket": ticket_to_dict(x, seat_names(db, t.id))}


def junban_accepting(db: Session, skey: str, body: dict) -> dict:
    user, t, m = staff_of(db, skey, body)
    on = body.get("on")
    if not isinstance(on, bool):
        raise PasskeyError("invalid request")
    s = db.query(model_switch.SwitchState).filter(
        model_switch.SwitchState.name == accepting_name(t.guid)).first()
    if s is None:
        s = model_switch.SwitchState(name=accepting_name(t.guid))
        db.add(s)
    s.value = 1 if on else 0
    s.updated_by = user
    s.updated_at = utcnow()
    db.commit()
    return {"status": "success", "accepting": on}


#
# 店の管理者
#

def seat_name_taken(db: Session, tenant_id: int, name: str, except_id: int = -1) -> bool:
    return db.query(Seat).filter(Seat.tenant_id == tenant_id, Seat.active == 1,
                                 Seat.name == name, Seat.id != except_id).first() is not None


def junban_seat_add(db: Session, skey: str, body: dict) -> dict:
    user, t = store_admin_of(db, skey, body)
    name = body.get("name")
    if not valid_user_name(name):
        raise PasskeyError("invalid seat name")
    if seat_name_taken(db, t.id, name):
        raise PasskeyError("seat already exists")
    top = db.query(func.max(Seat.sort_order)).filter(Seat.tenant_id == t.id).scalar()
    s = Seat(tenant_id=t.id, name=name, sort_order=(top or 0) + 1, active=1, created_at=utcnow())
    db.add(s)
    db.commit()
    return {"status": "success", "seat": seat_to_dict(s)}


def junban_seat_rename(db: Session, skey: str, body: dict) -> dict:
    user, t = store_admin_of(db, skey, body)
    s = get_active_seat(db, t.id, body)
    name = body.get("name")
    if not valid_user_name(name):
        raise PasskeyError("invalid seat name")
    if seat_name_taken(db, t.id, name, s.id):
        raise PasskeyError("seat already exists")
    s.name = name
    db.commit()
    return {"status": "success", "seat": seat_to_dict(s)}


def junban_seat_delete(db: Session, skey: str, body: dict) -> dict:
    user, t = store_admin_of(db, skey, body)
    s = get_active_seat(db, t.id, body)
    s.active = 0
    db.commit()
    return {"status": "success", "seat": s.id}


#
# routes
#

def route(path, func_):
    async def handler(request: Request,
                      authorization: str = Header(None),
                      db: Session = Depends(get_db)):
        return await arun(db, func_, extract_bearer_token(authorization), await read_body(request))
    handler.__name__ = "api_" + func_.__name__
    router.post(path)(handler)


route("/junban/v0/store", junban_store)
route("/junban/v0/take", junban_take)
route("/junban/v0/ticket", junban_ticket)
route("/junban/v0/cancel", junban_cancel)
route("/junban/v0/queue", junban_queue)
route("/junban/v0/call", junban_call)
route("/junban/v0/set_status", junban_set_status)
route("/junban/v0/accepting", junban_accepting)
route("/junban/v0/seat_add", junban_seat_add)
route("/junban/v0/seat_rename", junban_seat_rename)
route("/junban/v0/seat_delete", junban_seat_delete)
