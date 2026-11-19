#ifndef API_JUNBAN_H_
#define API_JUNBAN_H_

//
// 順番待ち（整理券）
//
// 店 = テナント（guid で指定）。お客さんはログイン不要、店員はそのテナントの承認済みメンバー。
//
// お客さん（ログイン不要）
//   /api/junban/v0/store        {tenant} 店名、受付中か、席の種類ごとの待ち組数、呼出中の番号
//   /api/junban/v0/take         {tenant, seat, people} 整理券を取る（token を返す）
//   /api/junban/v0/ticket       {token} 自分の整理券と「あと何組」
//   /api/junban/v0/cancel       {token} 取り消す（待ち・呼出中のときだけ）
//
// 店員（そのテナントの承認済みメンバー）
//   /api/junban/v0/queue        {tenant} 今日の整理券ぜんぶと席の種類
//   /api/junban/v0/call         {tenant, seat} その席の列でいちばん前の人を呼ぶ
//   /api/junban/v0/set_status   {tenant, number, status} 状態を変える（waiting/called/seated/skipped）
//   /api/junban/v0/accepting    {tenant, on} 受付の開始・停止
//
// 店の管理者（そのテナントの管理者）
//   /api/junban/v0/seat_add     {tenant, name} 席の種類を足す
//   /api/junban/v0/seat_rename  {tenant, seat, name} 名前を変える
//   /api/junban/v0/seat_delete  {tenant, seat} 消す（非表示にするだけ）
//
// 番号は店ごと・営業日ごとに 1 から、席の種類をまたいだ通し番号。営業日は日本時間の 4 時で切り替える。
// 席の種類が 1 つもない店は seat = 0（指定なし）の 1 列だけ。
// Python 版は python/testsv_api_junban.py。検査の順番と失敗時の message を揃えてある。
//

#include <algorithm>
#include <map>
#include <vector>

#include "testsv_api_tenant.h"
#include "model_junban.h"

namespace junban_api {

    using namespace passkey;
    using namespace tenant_api;

    static const char* ST_WAITING = "waiting";
    static const char* ST_CALLED = "called";
    static const char* ST_SEATED = "seated";
    static const char* ST_SKIPPED = "skipped";
    static const char* ST_CANCELED = "canceled";

    static const int64_t PEOPLE_MAX = 99;
    static const int TAKE_RETRY = 5;

    // 営業日（日本時間の 4 時で切り替え）= UTC + 9 時間 - 4 時間 の日付
    static inline int64_t today()
    {
        time_t t = time(NULL) + 5 * 3600;
        std::tm tm_buf;
#ifdef _WIN32
        gmtime_s(&tm_buf, &t);
#else
        gmtime_r(&t, &tm_buf);
#endif
        return (int64_t)(tm_buf.tm_year + 1900) * 10000 + (tm_buf.tm_mon + 1) * 100 + tm_buf.tm_mday;
    }

    static inline std::string accepting_name(const std::string& guid)
    {
        return "junban/" + guid;
    }

    // 受付中か。まだ一度も切り替えていなければ受付中
    static inline bool is_accepting(sql::connection& db, const std::string& guid)
    {
        const auto tab = test::switch_state::SwitchState{};
        for (const auto& a : db(select(tab.value).from(tab).where(tab.name == accepting_name(guid)))) {
            return a.value.value() == 1;
        }
        return true;
    }

    // 整数（真偽値や小数は不可）なら true
    static inline bool int_of(const json& body, const char* key, int64_t& out)
    {
        if (!body.is_object() || !body.contains(key) || !body[key].is_number_integer()) return false;
        out = body[key].get<int64_t>();
        return true;
    }

    struct SeatRow {
        int64_t id = -1;
        std::string name;
        int64_t sort_order = 0;
        bool active = false;
    };

    struct TicketRow {
        int64_t id = -1;
        int64_t tenant_id = 0, biz_day = 0, num = 0, seat_id = 0, people = 0;
        std::string status, token, created_at, called_at, done_at;
        bool called_null = true, done_null = true, created_null = true;
    };

    template <typename Row>
    static inline SeatRow to_seat(const Row& a)
    {
        SeatRow s;
        s.id = a.id;
        s.name = a.name.value();
        s.sort_order = a.sortOrder.value();
        s.active = a.active.value() == 1;
        return s;
    }

    template <typename Row>
    static inline TicketRow to_ticket(const Row& a)
    {
        TicketRow t;
        t.id = a.id;
        t.tenant_id = a.tenantId.value();
        t.biz_day = a.bizDay.value();
        t.num = a.num.value();
        t.seat_id = a.seatId.value();
        t.people = a.people.value();
        t.status = a.status.value();
        t.token = a.token.value();
        t.created_null = a.createdAt.is_null();
        t.created_at = a.createdAt.value();
        t.called_null = a.calledAt.is_null();
        t.called_at = a.calledAt.value();
        t.done_null = a.doneAt.is_null();
        t.done_at = a.doneAt.value();
        return t;
    }

    // 並びは sort_order、id の順
    static inline std::vector<SeatRow> all_seats(sql::connection& db, int64_t tenant_id, bool active_only)
    {
        const auto tab = test::junban::JunbanSeat{};
        std::vector<SeatRow> v;
        for (const auto& a : db(select(all_of(tab)).from(tab).where(tab.tenantId == tenant_id))) {
            SeatRow s = to_seat(a);
            if (active_only && !s.active) continue;
            v.push_back(s);
        }
        std::sort(v.begin(), v.end(), [](const SeatRow& x, const SeatRow& y) {
            return x.sort_order != y.sort_order ? x.sort_order < y.sort_order : x.id < y.id;
        });
        return v;
    }

    static inline std::map<int64_t, std::string> seat_names(sql::connection& db, int64_t tenant_id)
    {
        std::map<int64_t, std::string> m;
        for (const auto& s : all_seats(db, tenant_id, false)) m[s.id] = s.name;
        return m;
    }

    static inline SeatRow get_active_seat(sql::connection& db, int64_t tenant_id, const json& body)
    {
        int64_t seat;
        if (!int_of(body, "seat", seat)) throw PasskeyError("invalid request");
        const auto tab = test::junban::JunbanSeat{};
        for (const auto& a : db(select(all_of(tab)).from(tab)
            .where(tab.tenantId == tenant_id and tab.id == seat and tab.active == 1))) {
            return to_seat(a);
        }
        throw PasskeyError("unknown seat");
    }

    static inline json seat_to_json(const SeatRow& s)
    {
        json o = json::object();
        o["seat"] = s.id;
        o["name"] = s.name;
        o["active"] = s.active;
        return o;
    }

    static inline json ticket_to_json(const TicketRow& t, const std::map<int64_t, std::string>& names)
    {
        json o = json::object();
        o["number"] = t.num;
        o["seat"] = t.seat_id;
        auto it = names.find(t.seat_id);
        o["seat_name"] = it != names.end() ? json(it->second) : json(nullptr);
        o["people"] = t.people;
        o["status"] = t.status;
        o["created_at"] = t.created_null ? json(nullptr) : format_time(t.created_at);
        o["called_at"] = t.called_null ? json(nullptr) : format_time(t.called_at);
        o["done_at"] = t.done_null ? json(nullptr) : format_time(t.done_at);
        return o;
    }

    // 今日の整理券を番号順に
    static inline std::vector<TicketRow> today_tickets(sql::connection& db, int64_t tenant_id)
    {
        const auto tab = test::junban::JunbanTicket{};
        std::vector<TicketRow> v;
        for (const auto& a : db(select(all_of(tab)).from(tab)
            .where(tab.tenantId == tenant_id and tab.bizDay == today()))) {
            v.push_back(to_ticket(a));
        }
        std::sort(v.begin(), v.end(), [](const TicketRow& x, const TicketRow& y) { return x.num < y.num; });
        return v;
    }

    static inline TicketRow get_ticket_by_token(sql::connection& db, const json& body)
    {
        std::string token = str_of(body, "token");
        if (token == "") throw PasskeyError("invalid request");
        const auto tab = test::junban::JunbanTicket{};
        for (const auto& a : db(select(all_of(tab)).from(tab).where(tab.token == token))) {
            return to_ticket(a);
        }
        throw PasskeyError("unknown ticket");
    }

    static inline TicketRow get_today_ticket(sql::connection& db, int64_t tenant_id, const json& body)
    {
        int64_t num;
        if (!int_of(body, "number", num)) throw PasskeyError("invalid request");
        const auto tab = test::junban::JunbanTicket{};
        for (const auto& a : db(select(all_of(tab)).from(tab)
            .where(tab.tenantId == tenant_id and tab.bizDay == today() and tab.num == num))) {
            return to_ticket(a);
        }
        throw PasskeyError("unknown ticket");
    }

    // 1 枚読み直す（更新後の値を返すため）
    static inline TicketRow reload_ticket(sql::connection& db, int64_t id)
    {
        const auto tab = test::junban::JunbanTicket{};
        for (const auto& a : db(select(all_of(tab)).from(tab).where(tab.id == id))) {
            return to_ticket(a);
        }
        throw PasskeyError("unknown ticket");
    }

    static inline json calling_of(const std::vector<TicketRow>& tickets)
    {
        json a = json::array();
        for (const auto& x : tickets) if (x.status == ST_CALLED) a.push_back(x.num);
        return a;
    }

    // 店員の入口: ログインしていて、そのテナントの承認済みメンバー
    static inline TenantRow staff_of(sql::connection& db, const std::string& skey, const json& body,
                                     std::string& user, MemberRow& m)
    {
        user = session_user(db, skey);
        TenantRow t = get_tenant(db, body);
        m = tenant_member_of(db, t.id, user);
        return t;
    }

    // 店の管理者の入口
    static inline TenantRow store_admin_of(sql::connection& db, const std::string& skey, const json& body)
    {
        std::string user = session_user(db, skey);
        TenantRow t = get_tenant(db, body);
        tenant_admin_of(db, t.id, user);
        return t;
    }

    static inline json success_with_ticket(sql::connection& db, int64_t tenant_id, int64_t id)
    {
        json ret = json::object();
        ret["status"] = "success";
        ret["ticket"] = ticket_to_json(reload_ticket(db, id), seat_names(db, tenant_id));
        return ret;
    }

    //
    // お客さん
    //

    static inline json junban_store(sql::connection& db, const json& body,
                                    const std::string& skey, const Request& req)
    {
        TenantRow t = get_tenant(db, body);
        std::vector<TicketRow> tickets = today_tickets(db, t.id);
        json seats = json::array();
        for (const auto& s : all_seats(db, t.id, true)) {
            int64_t n = 0;
            for (const auto& x : tickets) if (x.seat_id == s.id && x.status == ST_WAITING) n++;
            json o = json::object();
            o["seat"] = s.id;
            o["name"] = s.name;
            o["waiting"] = n;
            seats.push_back(o);
        }
        int64_t waiting = 0;
        for (const auto& x : tickets) if (x.status == ST_WAITING) waiting++;
        json st = json::object();
        st["tenant"] = t.guid;
        st["name"] = t.name;
        st["accepting"] = is_accepting(db, t.guid);
        st["seats"] = seats;
        st["waiting"] = waiting;
        st["calling"] = calling_of(tickets);
        json ret = json::object();
        ret["status"] = "success";
        ret["store"] = st;
        return ret;
    }

    static inline json junban_take(sql::connection& db, const json& body,
                                   const std::string& skey, const Request& req)
    {
        TenantRow t = get_tenant(db, body);
        if (!is_accepting(db, t.guid)) throw PasskeyError("not accepting");
        int64_t people;
        if (!int_of(body, "people", people) || people < 1 || people > PEOPLE_MAX) {
            throw PasskeyError("invalid people");
        }
        int64_t seat;
        if (!int_of(body, "seat", seat)) throw PasskeyError("invalid request");
        std::vector<SeatRow> seats = all_seats(db, t.id, true);
        if (!seats.empty()) {
            bool found = false;
            for (const auto& s : seats) if (s.id == seat) found = true;
            if (!found) throw PasskeyError("unknown seat");
        }
        else if (seat != 0) {
            throw PasskeyError("unknown seat");
        }
        std::string token = to_hex(random_bytes(16));
        int64_t day = today();
        const auto tab = test::junban::JunbanTicket{};
        // 同時に受付した人と同じ番号になったら（一意制約で弾かれたら）振り直す
        for (int i = 0; i < TAKE_RETRY; i++) {
            int64_t top = 0;
            for (const auto& a : db(select(tab.num).from(tab).where(tab.tenantId == t.id and tab.bizDay == day))) {
                if (a.num.value() > top) top = a.num.value();
            }
            int64_t id;
            try {
                id = (int64_t)db(insert_into(tab).set(tab.tenantId = t.id, tab.bizDay = day, tab.num = top + 1,
                    tab.seatId = seat, tab.people = people, tab.status = ST_WAITING, tab.token = token,
                    tab.createdAt = time_to_db(time(NULL))));
            }
            catch (const sqlpp::exception&) {
                continue;
            }
            json ret = success_with_ticket(db, t.id, id);
            ret["token"] = token;
            return ret;
        }
        throw PasskeyError("busy");
    }

    static inline json junban_ticket(sql::connection& db, const json& body,
                                     const std::string& skey, const Request& req)
    {
        TicketRow x = get_ticket_by_token(db, body);
        const auto ttab = test::tenant::Tenant{};
        TenantRow t;
        for (const auto& a : db(select(all_of(ttab)).from(ttab).where(ttab.id == x.tenant_id))) {
            t = to_tenant(a);
            break;
        }
        if (t.id < 0) throw PasskeyError("unknown ticket");
        json d = ticket_to_json(x, seat_names(db, t.id));
        bool is_today = x.biz_day == today();
        d["ahead"] = nullptr;
        json calling = json::array();
        if (is_today) {
            std::vector<TicketRow> tickets = today_tickets(db, t.id);
            if (x.status == ST_WAITING) {
                int64_t n = 0;
                for (const auto& y : tickets) {
                    if (y.seat_id == x.seat_id && y.status == ST_WAITING && y.num < x.num) n++;
                }
                d["ahead"] = n;
            }
            calling = calling_of(tickets);
        }
        json st = json::object();
        st["tenant"] = t.guid;
        st["name"] = t.name;
        st["calling"] = calling;
        json ret = json::object();
        ret["status"] = "success";
        ret["ticket"] = d;
        ret["today"] = is_today;
        ret["store"] = st;
        return ret;
    }

    static inline json junban_cancel(sql::connection& db, const json& body,
                                     const std::string& skey, const Request& req)
    {
        TicketRow x = get_ticket_by_token(db, body);
        if (x.status != ST_WAITING && x.status != ST_CALLED) throw PasskeyError("cannot cancel");
        const auto tab = test::junban::JunbanTicket{};
        db(update(tab).set(tab.status = ST_CANCELED, tab.doneAt = time_to_db(time(NULL))).where(tab.id == x.id));
        return success_with_ticket(db, x.tenant_id, x.id);
    }

    //
    // 店員
    //

    static inline json junban_queue(sql::connection& db, const json& body,
                                    const std::string& skey, const Request& req)
    {
        std::string user;
        MemberRow m;
        TenantRow t = staff_of(db, skey, body, user, m);
        std::vector<SeatRow> seats = all_seats(db, t.id, false);
        std::map<int64_t, std::string> names;
        json sa = json::array();
        for (const auto& s : seats) {
            names[s.id] = s.name;
            sa.push_back(seat_to_json(s));
        }
        json ta = json::array();
        for (const auto& x : today_tickets(db, t.id)) ta.push_back(ticket_to_json(x, names));
        json ret = json::object();
        ret["status"] = "success";
        ret["tenant"] = t.guid;
        ret["name"] = t.name;
        ret["my_role"] = m.role_null ? json(nullptr) : json(m.role);
        ret["accepting"] = is_accepting(db, t.guid);
        ret["biz_day"] = today();
        ret["seats"] = sa;
        ret["tickets"] = ta;
        return ret;
    }

    static inline json junban_call(sql::connection& db, const json& body,
                                   const std::string& skey, const Request& req)
    {
        std::string user;
        MemberRow m;
        TenantRow t = staff_of(db, skey, body, user, m);
        int64_t seat;
        if (!int_of(body, "seat", seat)) throw PasskeyError("invalid request");
        int64_t id = -1;
        for (const auto& x : today_tickets(db, t.id)) {
            if (x.seat_id == seat && x.status == ST_WAITING) {
                id = x.id;
                break;
            }
        }
        if (id < 0) throw PasskeyError("no one waiting");
        const auto tab = test::junban::JunbanTicket{};
        db(update(tab).set(tab.status = ST_CALLED, tab.calledAt = time_to_db(time(NULL))).where(tab.id == id));
        return success_with_ticket(db, t.id, id);
    }

    static inline json junban_set_status(sql::connection& db, const json& body,
                                         const std::string& skey, const Request& req)
    {
        std::string user;
        MemberRow m;
        TenantRow t = staff_of(db, skey, body, user, m);
        TicketRow x = get_today_ticket(db, t.id, body);
        std::string status = str_of(body, "status");
        if (status != ST_WAITING && status != ST_CALLED && status != ST_SEATED && status != ST_SKIPPED) {
            throw PasskeyError("invalid status");
        }
        const auto tab = test::junban::JunbanTicket{};
        std::string now = time_to_db(time(NULL));
        if (status == ST_WAITING) {
            db(update(tab).set(tab.status = status, tab.calledAt = sqlpp::null, tab.doneAt = sqlpp::null)
                .where(tab.id == x.id));
        }
        else if (status == ST_CALLED) {
            db(update(tab).set(tab.status = status, tab.calledAt = now, tab.doneAt = sqlpp::null)
                .where(tab.id == x.id));
        }
        else {
            db(update(tab).set(tab.status = status, tab.doneAt = now).where(tab.id == x.id));
        }
        return success_with_ticket(db, t.id, x.id);
    }

    static inline json junban_accepting(sql::connection& db, const json& body,
                                        const std::string& skey, const Request& req)
    {
        std::string user;
        MemberRow m;
        TenantRow t = staff_of(db, skey, body, user, m);
        if (!body.contains("on") || !body["on"].is_boolean()) throw PasskeyError("invalid request");
        bool on = body["on"].get<bool>();
        std::string now = time_to_db(time(NULL));
        std::string name = accepting_name(t.guid);
        const auto tab = test::switch_state::SwitchState{};
        if (!db(select(tab.id).from(tab).where(tab.name == name)).empty()) {
            db(update(tab).set(tab.value = on ? 1 : 0, tab.updatedBy = user, tab.updatedAt = now)
                .where(tab.name == name));
        }
        else {
            db(insert_into(tab).set(tab.name = name, tab.value = on ? 1 : 0, tab.updatedBy = user,
                tab.updatedAt = now));
        }
        json ret = json::object();
        ret["status"] = "success";
        ret["accepting"] = on;
        return ret;
    }

    //
    // 店の管理者
    //

    static inline bool seat_name_taken(sql::connection& db, int64_t tenant_id, const std::string& name,
                                       int64_t except_id = -1)
    {
        for (const auto& s : all_seats(db, tenant_id, true)) {
            if (s.name == name && s.id != except_id) return true;
        }
        return false;
    }

    static inline json success_with_seat(sql::connection& db, int64_t tenant_id, int64_t id)
    {
        for (const auto& s : all_seats(db, tenant_id, false)) {
            if (s.id == id) {
                json ret = json::object();
                ret["status"] = "success";
                ret["seat"] = seat_to_json(s);
                return ret;
            }
        }
        throw PasskeyError("unknown seat");
    }

    static inline json junban_seat_add(sql::connection& db, const json& body,
                                       const std::string& skey, const Request& req)
    {
        TenantRow t = store_admin_of(db, skey, body);
        if (!body.contains("name") || !valid_user_name(body["name"])) throw PasskeyError("invalid seat name");
        std::string name = body["name"];
        if (seat_name_taken(db, t.id, name)) throw PasskeyError("seat already exists");
        int64_t top = 0;
        for (const auto& s : all_seats(db, t.id, false)) if (s.sort_order > top) top = s.sort_order;
        const auto tab = test::junban::JunbanSeat{};
        int64_t id = (int64_t)db(insert_into(tab).set(tab.tenantId = t.id, tab.name = name,
            tab.sortOrder = top + 1, tab.active = 1, tab.createdAt = time_to_db(time(NULL))));
        return success_with_seat(db, t.id, id);
    }

    static inline json junban_seat_rename(sql::connection& db, const json& body,
                                          const std::string& skey, const Request& req)
    {
        TenantRow t = store_admin_of(db, skey, body);
        SeatRow s = get_active_seat(db, t.id, body);
        if (!body.contains("name") || !valid_user_name(body["name"])) throw PasskeyError("invalid seat name");
        std::string name = body["name"];
        if (seat_name_taken(db, t.id, name, s.id)) throw PasskeyError("seat already exists");
        const auto tab = test::junban::JunbanSeat{};
        db(update(tab).set(tab.name = name).where(tab.id == s.id));
        return success_with_seat(db, t.id, s.id);
    }

    static inline json junban_seat_delete(sql::connection& db, const json& body,
                                          const std::string& skey, const Request& req)
    {
        TenantRow t = store_admin_of(db, skey, body);
        SeatRow s = get_active_seat(db, t.id, body);
        const auto tab = test::junban::JunbanSeat{};
        db(update(tab).set(tab.active = 0).where(tab.id == s.id));
        json ret = json::object();
        ret["status"] = "success";
        ret["seat"] = s.id;
        return ret;
    }

    //
    // テーブル作成と routes
    //

    // (tenant_id, biz_day, num) は一意（同時に受付しても同じ番号を出さない）。
    // Python 版（SQLAlchemy の create_all）と同じ形にしてある。どちらが先に作っても同じ表になる。
    static inline void init_junban_db()
    {
        sql::connection& db = get_sql_connection();
#ifdef USE_MYSQL
        db.execute(R"(
        CREATE TABLE IF NOT EXISTS junban_seat (
        id int PRIMARY KEY AUTO_INCREMENT,
        tenant_id int NOT NULL,
        name TEXT NOT NULL,
        sort_order int NOT NULL,
        active int NOT NULL,
        created_at DateTime)
        )");
        db.execute(R"(
        CREATE TABLE IF NOT EXISTS junban_ticket (
        id int PRIMARY KEY AUTO_INCREMENT,
        tenant_id int NOT NULL,
        biz_day int NOT NULL,
        num int NOT NULL,
        seat_id int NOT NULL,
        people int NOT NULL,
        status TEXT NOT NULL,
        token TEXT NOT NULL,
        created_at DateTime,
        called_at DateTime,
        done_at DateTime,
        CONSTRAINT uq_junban_ticket UNIQUE (tenant_id, biz_day, num))
        )");
#else
        db.execute(R"(
        CREATE TABLE IF NOT EXISTS junban_seat (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        sort_order INTEGER NOT NULL,
        active INTEGER NOT NULL,
        created_at TEXT)
        )");
        db.execute(R"(
        CREATE TABLE IF NOT EXISTS junban_ticket (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        biz_day INTEGER NOT NULL,
        num INTEGER NOT NULL,
        seat_id INTEGER NOT NULL,
        people INTEGER NOT NULL,
        status TEXT NOT NULL,
        token TEXT NOT NULL,
        created_at TEXT,
        called_at TEXT,
        done_at TEXT,
        CONSTRAINT uq_junban_ticket UNIQUE (tenant_id, biz_day, num))
        )");
#endif
    }

    inline static void init_api_junban(Server& svr)
    {
        init_junban_db();
        add_route(svr, "/api/junban/v0/store", junban_store);
        add_route(svr, "/api/junban/v0/take", junban_take);
        add_route(svr, "/api/junban/v0/ticket", junban_ticket);
        add_route(svr, "/api/junban/v0/cancel", junban_cancel);
        add_route(svr, "/api/junban/v0/queue", junban_queue);
        add_route(svr, "/api/junban/v0/call", junban_call);
        add_route(svr, "/api/junban/v0/set_status", junban_set_status);
        add_route(svr, "/api/junban/v0/accepting", junban_accepting);
        add_route(svr, "/api/junban/v0/seat_add", junban_seat_add);
        add_route(svr, "/api/junban/v0/seat_rename", junban_seat_rename);
        add_route(svr, "/api/junban/v0/seat_delete", junban_seat_delete);
    }

}

#endif
