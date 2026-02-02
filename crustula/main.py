"""Main API for Crustula cookie jar service."""

import datetime
from fastapi import Depends, FastAPI, HTTPException
from sqlmodel import Field, Relationship, Session, SQLModel, create_engine, select
from . import curl_to_cookies_txt

class JarBase(SQLModel):
    domain: str = Field(index=True)
    cookies: str
    # TODO index?
    ctime: datetime.datetime | None = None
    mtime: datetime.datetime | None = None
    atime: datetime.datetime | None = None


class Jar(JarBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    calls: list["Call"] = Relationship(back_populates="jar")


class JarCreate(SQLModel):
    curl_cmd: str


class JarPublic(JarBase):
    id: int


class CallBase(SQLModel):
    domain: str = Field(index=True)
    url: str | None = None
    timestamp: datetime.datetime | None = None  # TODO index?
    success: bool | None = Field(default=None, index=True)

    jar_id: int | None = Field(default=None, foreign_key="jar.id")


class Call(CallBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    jar: Jar | None = Relationship(back_populates="calls")

class CallPublic(CallBase):
    id: int

class CallUpdate(SQLModel):
    # TODO allow cookies update
    url: str | None = None
    timestamp: datetime.datetime | None = None  # TODO allow this?
    success: bool | None = None

class CallPublicWithJar(CallPublic):
    jar: JarPublic | None = None


class JarPublicWithCalls(JarPublic):
    calls: list[CallPublicWithJar] = []

class DomainPublic(SQLModel):
    domain: str
    active_jar: JarPublic| None = None
    recent_calls: list[CallPublic] = []

SQLLITE_URL = "sqlite:///crustula.db"

connect_args = {"check_same_thread": False}
engine = create_engine(SQLLITE_URL, echo=True, connect_args=connect_args)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


app = FastAPI()


@app.on_event("startup")
def on_startup():
    create_db_and_tables()

def jar_for_domain(session: Session, domain: str) -> Jar | None:
    jars = session.exec(select(Jar).where(Jar.domain == domain)).all()
    if not jars:
        return None
    jar_stat = [(jar_stats(jar), jar) for jar in jars]
    jar_stat.sort(key=lambda x: x[0][1], reverse=True)
    if jar_stat[0][0][1] and jar_stat[0][0][2] < -2:
        return None
    return jar_stat[0][1]


def domain_from_url(url: str) -> str:
    # TODO better url parsing
    return url.split("/")[2]

def jar_stats(jar: Jar) -> tuple[bool, datetime.datetime, int]:
    calls = jar.calls
    if not calls:
        assert jar.ctime is not None
        return (True, jar.ctime, 0)
    assert all(call.timestamp is not None for call in calls)
    calls.sort(key=lambda call: call.timestamp, reverse=True)
    receht_call = calls[0]
    success = receht_call.success if receht_call.success is not None else True
    last_used = calls[0].timestamp
    strikes = sum(1 for call in calls if call.success is False)
    strikes = strikes * -1
    assert last_used is not None
    return (success, last_used, strikes)

@app.get("/cookies/", response_model=CallPublicWithJar)
def get_cookies(*, session: Session = Depends(get_session), url: str):
    domain = domain_from_url(url)
    jar = jar_for_domain(session, domain)
    if not jar:
        raise HTTPException(status_code=404, detail="No cookies for this domain")
    db_call = Call(domain=domain,
                   url=url,
                   timestamp=datetime.datetime.now(),
                   success=None,
                   jar_id=jar.id)
    db_call.jar = jar
    session.add(db_call)
    session.commit()
    session.refresh(db_call)
    return db_call


@app.patch("/calls/{call_id}", response_model=CallPublic)
def update_call(
    *, session: Session = Depends(get_session), call_id: int, call: CallUpdate
):
    # TODO handle cookies update
    db_call = session.get(Call, call_id)
    if not db_call:
        raise HTTPException(status_code=404, detail="Call not found")
    call_data = call.model_dump(exclude_unset=True)
    db_call.sqlmodel_update(call_data)
    session.add(db_call)
    session.commit()
    session.refresh(db_call)
    return db_call


@app.post("/cookies/", response_model=JarPublic)
def create_jar(*, session: Session = Depends(get_session), jar: JarCreate):
    url, cookie_header = curl_to_cookies_txt.extract_curl_string(jar.curl_cmd)
    cookies_txt = curl_to_cookies_txt.convert_header_to_cookies_str(cookie_header)
    db_jar = Jar(cookies=cookies_txt,
                 domain=domain_from_url(url),
                 ctime=datetime.datetime.now(),
                 mtime=None,
                 atime=None)
    session.add(db_jar)
    session.commit()
    session.refresh(db_jar)
    return db_jar


@app.get("/domains/", response_model=list[DomainPublic])
def read_domains(
    *,
    session: Session = Depends(get_session)):
    all_domains = set()
    # TODO populate from jars and calls
    domains = [DomainPublic(domain=domain,
                            active_jar=jar_for_domain(session, domain),  # TODO convert to public
                            recent_calls=[]) for domain in all_domains]
    # TODO include recent calls
    return domains

@app.delete("/jars/{jar_id}")
def delete_jar(*, session: Session = Depends(get_session), jar_id: int):
    jar = session.get(Jar, jar_id)
    # TODO delete associated calls
    if not jar:
        raise HTTPException(status_code=404, detail="Jar not found")
    session.delete(jar)
    session.commit()
    return {"ok": True}
