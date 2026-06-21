"""pytests for crustula"""

import pytest
from sqlalchemy import inspect, text
from fastapi.testclient import TestClient
from sqlmodel import create_engine, Session, SQLModel

from crustula import main as api
from crustula import curl_to_cookies_txt
from crustula import migrate as migration


@pytest.fixture
def client(tmp_path):
    # create a fresh sqlite database for tests (shared across threads)
    db_file = tmp_path / "test.db"
    url = f"sqlite:///{db_file}"
    engine = create_engine(url, connect_args=api.connect_args)
    # replace api engine and recreate tables
    api.engine = engine
    SQLModel.metadata.create_all(engine)

    def get_session_override():
        with Session(engine) as s:
            yield s

    api.app.dependency_overrides[api.get_session] = get_session_override
    with TestClient(api.app) as c:
        yield c


TESTING_CURL_CMD = """curl 'https://www.something.com/' \
  -H 'User-Agent: Mozilla/5.0' \
  -H 'Cookie: cookiea=a; cookieb=b' \
"""

# Chromium "Copy as cURL" style: lowercase header names, --compressed, -b for cookies
CHROMIUM_CURL_CMD = """curl 'https://www.chrome.com/' \
  --compressed \
  -H 'user-agent: Mozilla/5.0' \
  -b 'cookiec=c; cookied=d' \
"""


def test_basic(client: TestClient):
    # create jar
    response = client.post("/cookies/", json={"curl_cmd": TESTING_CURL_CMD})
    assert response.status_code == 200
    jar_data = response.json()
    assert jar_data["domain"] == "something.com"

    # get cookies -- flattened response (#19)
    response = client.get("/cookies/", params={"url": "https://www.something.com/page"})
    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "something.com"
    assert data["call_id"]
    assert data["jar_id"] == jar_data["id"]
    # TODO: improve cookie parsing to get names/values
    assert "cookiea" in data["cookies"]
    assert "cookieb" in data["cookies"]
    # other headers are captured (#7)
    assert data["headers"]["User-Agent"] == "Mozilla/5.0"

    response = client.get("/domains/")
    assert response.status_code == 200
    domains = response.json()
    matching_domains = [x for x in domains if x['domain'] == "something.com"]
    assert len(matching_domains) == 1
    assert matching_domains[0]['active_jar']
    assert matching_domains[0]['recent_calls']
    assert matching_domains[0]['active_jar']['id'] == data['jar_id']


def test_get_cookies_miss(client: TestClient):
    response = client.get("/cookies/", params={"url": "https://www.nope.com/"})
    assert response.status_code == 404


def test_patch_call_writes_cookies_back(client: TestClient):
    client.post("/cookies/", json={"curl_cmd": TESTING_CURL_CMD})
    data = client.get("/cookies/",
                      params={"url": "https://www.something.com/page"}).json()
    call_id = data["call_id"]

    # report success and push refreshed cookies via a fresh curl (#10)
    refreshed = TESTING_CURL_CMD.replace("cookiea=a", "cookiea=NEW")
    response = client.patch(f"/calls/{call_id}",
                            json={"success": True, "curl_cmd": refreshed})
    assert response.status_code == 200
    assert response.json()["success"] is True

    # the jar now serves the refreshed cookies
    data = client.get("/cookies/",
                      params={"url": "https://www.something.com/page"}).json()
    assert "cookiea\tNEW" in data["cookies"]


def test_patch_call_cookieless_curl_does_not_wipe_jar(client: TestClient):
    client.post("/cookies/", json={"curl_cmd": TESTING_CURL_CMD})
    data = client.get("/cookies/",
                      params={"url": "https://www.something.com/page"}).json()
    call_id = data["call_id"]
    original = data["cookies"]

    # a refresh whose curl has no Cookie header must be rejected, not silently wipe
    cookieless = "curl 'https://www.something.com/' -H 'User-Agent: Mozilla/5.0'"
    response = client.patch(f"/calls/{call_id}",
                            json={"success": True, "curl_cmd": cookieless})
    assert response.status_code == 400

    data = client.get("/cookies/",
                      params={"url": "https://www.something.com/page"}).json()
    assert data["cookies"] == original


def test_account_crud_and_link(client: TestClient):
    acct = client.post("/accounts/", json={"domain": "something.com",
                                           "label": "favs",
                                           "username": "u",
                                           "password": "p"}).json()
    assert acct["id"]

    # link a jar to the account
    jar = client.post("/cookies/", json={"curl_cmd": TESTING_CURL_CMD,
                                         "account_id": acct["id"]}).json()
    assert jar["account_id"] == acct["id"]

    # account surfaced on the cookies response and selectable by label (#5)
    data = client.get("/cookies/", params={"url": "https://www.something.com/page",
                                           "label": "favs"}).json()
    assert data["account"]["username"] == "u"

    # patch + filtered list
    client.patch(f"/accounts/{acct['id']}", json={"password": "p2"})
    assert client.get(f"/accounts/{acct['id']}").json()["password"] == "p2"
    listed = client.get("/accounts/", params={"domain": "something.com"}).json()
    assert len(listed) == 1

    # delete unlinks the jar instead of deleting it
    assert client.delete(f"/accounts/{acct['id']}").status_code == 200
    jar = client.get("/cookies/",
                     params={"url": "https://www.something.com/page"}).json()
    assert jar["account"] is None


def test_delete_jar_cascades_calls(client: TestClient):
    jar = client.post("/cookies/", json={"curl_cmd": TESTING_CURL_CMD}).json()
    data = client.get("/cookies/",
                      params={"url": "https://www.something.com/page"}).json()
    call_id = data["call_id"]

    assert client.delete(f"/jars/{jar['id']}").status_code == 200
    # call is gone; patching it now 404s
    assert client.patch(f"/calls/{call_id}", json={"success": True}).status_code == 404


def test_get_single_domain(client: TestClient):
    client.post("/cookies/", json={"curl_cmd": TESTING_CURL_CMD})
    response = client.get("/domains/something.com")
    assert response.status_code == 200
    assert response.json()["domain"] == "something.com"
    assert response.json()["active_jar"]


def test_chromium_curl_parsing():
    url, cookies, headers = curl_to_cookies_txt.extract_curl_string(CHROMIUM_CURL_CMD)
    assert url == "https://www.chrome.com/"
    assert "cookiec=c" in cookies and "cookied=d" in cookies
    assert headers["user-agent"] == "Mozilla/5.0"


def test_migrate(tmp_path):
    # build a legacy DB: a jar table without headers/account_id, and no account table
    db_file = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args=api.connect_args)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE jar (id INTEGER PRIMARY KEY, domain VARCHAR, "
            "cookies VARCHAR, ctime DATETIME, mtime DATETIME, atime DATETIME)"))
        conn.execute(text(
            "INSERT INTO jar (domain, cookies) VALUES ('old.com', 'legacy')"))

    applied = migration.migrate(engine)
    assert "created table account" in applied
    assert "added column jar.headers" in applied
    assert "added column jar.account_id" in applied

    jar_cols = {c["name"] for c in inspect(engine).get_columns("jar")}
    assert {"headers", "account_id"} <= jar_cols
    assert inspect(engine).has_table("account")
    # existing row preserved
    with engine.connect() as conn:
        assert conn.execute(text("SELECT domain FROM jar")).scalar() == "old.com"

    # idempotent: a second run changes nothing
    assert migration.migrate(engine) == []
