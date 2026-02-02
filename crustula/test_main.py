import datetime
import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine, Session, SQLModel

from crustula import main as api


@pytest.fixture
def client(tmp_path):
    # create a fresh sqlite database for tests (shared across threads)
    db_file = tmp_path / "test.db"
    url = f"sqlite:///{db_file}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
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

def test_basic(client: TestClient):
    # create jar
    response = client.post("/cookies/", json={"curl_cmd": TESTING_CURL_CMD})
    assert response.status_code == 200
    jar_data = response.json()
    assert jar_data["domain"] == "www.something.com"

    # get cookies
    response = client.get("/cookies/", params={"url": "https://www.something.com/page"})
    assert response.status_code == 200
    jar_data = response.json()
    assert jar_data["domain"] == "www.something.com"
    assert jar_data["jar"]["domain"] == "www.something.com"
    # TODO: improve cookie parsing to get names/values
    assert "cookiea" in jar_data["jar"]["cookies"]
    assert "cookieb" in jar_data["jar"]["cookies"]

    #response = client.get("/cookies/www.something.com/")
    #assert response.status_code == 200
    #jar_data = response.json()
    #assert jar_data["id"] == 1
    #assert jar_data["domain"] == "www.something.com"

    # make a call
    #response = client.post("/calls/", json={"domain": "www.something.com", "url": "https://www.something.com/page"})
    #assert response.status_code == 200
    #call_data = response.json()
    #assert call_data["id"] == 1
    #assert call_data["jar"]["id"] == 1