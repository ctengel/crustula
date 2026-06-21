"""Main API for Crustula cookie jar service."""

import datetime
import json
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlmodel import Field, Relationship, Session, SQLModel, create_engine, select
import tldextract
from . import curl_to_cookies_txt


class AccountBase(SQLModel):
    domain: str = Field(index=True)
    label: str | None = None  # e.g. "favs" vs "bot" (multiuser per domain)
    username: str | None = None
    password: str | None = None  # stored as plain text for now
    ctime: datetime.datetime | None = None
    mtime: datetime.datetime | None = None


class Account(AccountBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    jars: list["Jar"] = Relationship(back_populates="account")


class AccountCreate(AccountBase):
    pass


class AccountPublic(AccountBase):
    id: int


class AccountUpdate(SQLModel):
    label: str | None = None
    username: str | None = None
    password: str | None = None


class JarBase(SQLModel):
    domain: str = Field(index=True)
    cookies: str
    headers: str | None = None  # JSON-encoded non-cookie headers (e.g. User-Agent)
    # TODO index?
    ctime: datetime.datetime | None = None
    mtime: datetime.datetime | None = None
    atime: datetime.datetime | None = None
    account_id: int | None = Field(default=None, foreign_key="account.id")


class Jar(JarBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    calls: list["Call"] = Relationship(back_populates="jar")
    account: Account | None = Relationship(back_populates="jars")


class JarCreate(SQLModel):
    curl_cmd: str
    account_id: int | None = None


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
    url: str | None = None
    timestamp: datetime.datetime | None = None
    success: bool | None = None
    # refreshed cookies to write back to the linked jar (either form accepted)
    cookies: str | None = None  # raw Netscape cookies.txt
    curl_cmd: str | None = None  # a fresh curl command to extract cookies from

class CallPublicWithJar(CallPublic):
    jar: JarPublic | None = None


class JarPublicWithCalls(JarPublic):
    calls: list[CallPublicWithJar] = []

# Flattened, intuitive response for GET /cookies/ (#19): cookies up front plus a
# call_id for the follow-up PATCH, instead of a Call wrapper containing a Jar.
class CookiesPublic(SQLModel):
    call_id: int
    jar_id: int
    domain: str
    cookies: str
    headers: dict[str, str] | None = None
    account: AccountPublic | None = None

class DomainPublic(SQLModel):
    domain: str
    active_jar: JarPublic | None = None
    accounts: list[AccountPublic] = []
    recent_calls: list[CallPublic] = []

SQLLITE_URL = "sqlite:///crustula.db"

connect_args = {"check_same_thread": False}
engine = create_engine(SQLLITE_URL, echo=True, connect_args=connect_args)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB tables on startup"""
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(title="crustula cookie jar", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")


def jar_for_domain(session: Session, domain: str,
                   account_id: int | None = None,
                   label: str | None = None) -> Jar | None:
    statement = select(Jar).where(Jar.domain == domain)
    if account_id is not None:
        statement = statement.where(Jar.account_id == account_id)
    if label is not None:
        statement = statement.join(Account).where(Account.label == label)
    jars = session.exec(statement).all()
    if not jars:
        return None
    jar_stat = [(jar_stats(jar), jar) for jar in jars]
    jar_stat.sort(key=lambda x: x[0][1], reverse=True)
    if jar_stat[0][0][1] and jar_stat[0][0][2] < -2:
        return None
    return jar_stat[0][1]


def domain_from_url(url: str) -> str:
    # TODO consider the "private" list and top_domain_under_public_suffix
    return tldextract.extract(url).registered_domain

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

@app.get("/cookies/", response_model=CookiesPublic)
def get_cookies(*, session: Session = Depends(get_session), url: str,
                account_id: int | None = None, label: str | None = None):
    domain = domain_from_url(url)
    jar = jar_for_domain(session, domain, account_id=account_id, label=label)
    if not jar:
        # still record the miss so jar selection can learn from it
        db_call = Call(domain=domain,
                       url=url,
                       timestamp=datetime.datetime.now(),
                       success=None,
                       jar_id=None)
        session.add(db_call)
        session.commit()
        raise HTTPException(status_code=404, detail="No cookies for this domain")
    db_call = Call(domain=domain,
                   url=url,
                   timestamp=datetime.datetime.now(),
                   success=None,
                   jar_id=jar.id)
    session.add(db_call)
    session.commit()
    session.refresh(db_call)
    return CookiesPublic(call_id=db_call.id,
                         jar_id=jar.id,
                         domain=domain,
                         cookies=jar.cookies,
                         headers=json.loads(jar.headers) if jar.headers else None,
                         account=jar.account)


@app.patch("/calls/{call_id}", response_model=CallPublic)
def update_call(
    *, session: Session = Depends(get_session), call_id: int, call: CallUpdate
):
    db_call = session.get(Call, call_id)
    if not db_call:
        raise HTTPException(status_code=404, detail="Call not found")
    call_data = call.model_dump(exclude_unset=True)
    # cookies/curl_cmd are not Call columns; they refresh the linked jar (#10)
    new_cookies = call_data.pop("cookies", None)
    curl_cmd = call_data.pop("curl_cmd", None)
    db_call.sqlmodel_update(call_data)
    if curl_cmd is not None:
        new_cookies = curl_to_cookies_txt.cookies_txt_from_curl(curl_cmd)
        if new_cookies is None:
            raise HTTPException(status_code=400,
                                detail="curl command has no Cookie header")
    if new_cookies:  # empty string never clobbers stored cookies
        if db_call.jar_id is None:
            raise HTTPException(status_code=400,
                                detail="Cannot update cookies: call has no linked jar")
        jar = session.get(Jar, db_call.jar_id)
        if jar is None:
            raise HTTPException(status_code=404, detail="Linked jar not found")
        jar.cookies = new_cookies
        jar.mtime = datetime.datetime.now()
        session.add(jar)
    session.add(db_call)
    session.commit()
    session.refresh(db_call)
    return db_call


@app.post("/cookies/", response_model=JarPublic)
def create_jar(*, session: Session = Depends(get_session), jar: JarCreate):
    url, cookie_header, other_headers = curl_to_cookies_txt.extract_curl_string(jar.curl_cmd)
    cookies_txt = curl_to_cookies_txt.convert_header_to_cookies_str(cookie_header)
    db_jar = Jar(cookies=cookies_txt,
                 headers=json.dumps(other_headers) if other_headers else None,
                 account_id=jar.account_id,
                 domain=domain_from_url(url),
                 ctime=datetime.datetime.now(),
                 mtime=None,
                 atime=None)
    session.add(db_jar)
    session.commit()
    session.refresh(db_jar)
    return db_jar


def domain_public(session: Session, domain: str) -> DomainPublic:
    return DomainPublic(
        domain=domain,
        active_jar=jar_for_domain(session, domain),
        accounts=session.exec(select(Account).where(Account.domain == domain)).all(),
        recent_calls=session.exec(
            select(Call).where(Call.domain == domain)
            .order_by(Call.timestamp.desc()).limit(3)).all())


@app.get("/domains/", response_model=list[DomainPublic])
def read_domains(
    *,
    session: Session = Depends(get_session)):
    # TODO eval efficiency of this
    all_domains = set()
    all_domains.update(x.domain for x in session.exec(select(Jar)))
    all_domains.update(x.domain for x in session.exec(select(Call)))
    all_domains.update(x.domain for x in session.exec(select(Account)))
    return [domain_public(session, domain) for domain in all_domains]


@app.get("/domains/{domain}", response_model=DomainPublic)
def read_domain(*, session: Session = Depends(get_session), domain: str):
    return domain_public(session, domain)


@app.delete("/jars/{jar_id}")
def delete_jar(*, session: Session = Depends(get_session), jar_id: int):
    jar = session.get(Jar, jar_id)
    if not jar:
        raise HTTPException(status_code=404, detail="Jar not found")
    # delete associated calls first (#11)
    for call in session.exec(select(Call).where(Call.jar_id == jar_id)).all():
        session.delete(call)
    session.delete(jar)
    session.commit()
    return {"ok": True}


@app.post("/accounts/", response_model=AccountPublic)
def create_account(*, session: Session = Depends(get_session), account: AccountCreate):
    db_account = Account.model_validate(
        account, update={"ctime": datetime.datetime.now()})
    session.add(db_account)
    session.commit()
    session.refresh(db_account)
    return db_account


@app.get("/accounts/", response_model=list[AccountPublic])
def read_accounts(*, session: Session = Depends(get_session), domain: str | None = None):
    statement = select(Account)
    if domain is not None:
        statement = statement.where(Account.domain == domain)
    return session.exec(statement).all()


@app.get("/accounts/{account_id}", response_model=AccountPublic)
def read_account(*, session: Session = Depends(get_session), account_id: int):
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@app.patch("/accounts/{account_id}", response_model=AccountPublic)
def update_account(
    *, session: Session = Depends(get_session), account_id: int, account: AccountUpdate
):
    db_account = session.get(Account, account_id)
    if not db_account:
        raise HTTPException(status_code=404, detail="Account not found")
    db_account.sqlmodel_update(account.model_dump(exclude_unset=True))
    db_account.mtime = datetime.datetime.now()
    session.add(db_account)
    session.commit()
    session.refresh(db_account)
    return db_account


@app.delete("/accounts/{account_id}")
def delete_account(*, session: Session = Depends(get_session), account_id: int):
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    # unlink jars that referenced this account rather than deleting them
    for jar in session.exec(select(Jar).where(Jar.account_id == account_id)).all():
        jar.account_id = None
        session.add(jar)
    session.delete(account)
    session.commit()
    return {"ok": True}
