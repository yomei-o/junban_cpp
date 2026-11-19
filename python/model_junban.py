from sqlalchemy import (
    Column,
    Integer,
    Text,
    DateTime,
    UniqueConstraint,
)
from database import Base


# 席の種類（カウンター / テーブル など）。店（テナント）ごとに持つ。
#   消すときは active = 0 にするだけ（その日の整理券から席名を引けるように）
class JunbanSeat(Base):
    __tablename__ = "junban_seat"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="ID")
    tenant_id = Column(Integer, nullable=False)
    name = Column(Text, nullable=False)
    sort_order = Column(Integer, nullable=False)
    active = Column(Integer, nullable=False)
    created_at = Column(DateTime)


# 整理券。番号 num は店ごと・営業日 biz_day（日本時間 4 時で切り替え、yyyymmdd）ごとに 1 から。
#   seat_id は 0 なら「席の指定なし」（席の種類がない店）
#   status は waiting（待ち）/ called（呼出中）/ seated（案内済み）/ skipped（不在）/ canceled（取消）
#   token はお客さんの待ち画面の URL に入れるランダムな 32 桁の 16 進数
#   (tenant_id, biz_day, num) は重複禁止。同時に受付しても同じ番号は出ない（出たら振り直す）
class JunbanTicket(Base):
    __tablename__ = "junban_ticket"
    __table_args__ = (UniqueConstraint("tenant_id", "biz_day", "num", name="uq_junban_ticket"),)

    id = Column(Integer, primary_key=True, autoincrement=True, comment="ID")
    tenant_id = Column(Integer, nullable=False)
    biz_day = Column(Integer, nullable=False)
    num = Column(Integer, nullable=False)
    seat_id = Column(Integer, nullable=False)
    people = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    token = Column(Text, nullable=False)
    created_at = Column(DateTime)
    called_at = Column(DateTime)
    done_at = Column(DateTime)
