"""Синхронизация структуры с Active Directory (как в «По Делу», ADR-0044 этап 4) — каталог ldap3 MOCK_SYNC."""

import uuid

import pytest
from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, Connection, Server
from sqlalchemy import select

from app.extensions import db
from app.models import Department, DepartmentHead, User, UserSession
from app.services import ad_sync
from app.services.ad_sync import AdConfig, entries_to_rows
from app.services.org import OrgSnapshot
from tests.conftest import login, make_user, new_client

BASE = "OU=Staff,DC=example,DC=local"
GUID = {name: str(uuid.UUID(int=index + 1)) for index, name in enumerate(("head", "seller", "gone", "newbie", "ceo"))}


def _attrs(guid, mail, name, department, manager=None, uac=512, title=None, login=None):
    attrs = {
        "objectClass": ["top", "person", "organizationalPerson", "user"], "objectCategory": "person",
        "objectGUID": uuid.UUID(guid).bytes_le, "mail": mail, "displayName": name,
        "sAMAccountName": login or mail.split("@")[0], "company": "Компания", "department": department,
        "userAccountControl": uac,
    }
    if manager:
        attrs["manager"] = manager
    if title:
        attrs["title"] = title
    return attrs


HEAD_DN = f"CN=Head\\, Sales,OU=Sales,{BASE}"
DIRECTORY = [
    (f"CN=CEO,{BASE}", _attrs(GUID["ceo"], "ceo@example.com", "Генеральный Директор", None, title="CEO")),
    (HEAD_DN, _attrs(GUID["head"], "Head@Example.com", "Начальник Продаж", "Продажи", manager=f"CN=CEO,{BASE}")),
    (f"CN=Seller,OU=Sales,{BASE}", _attrs(GUID["seller"], "seller@example.com", "Продавец Новый", "Продажи",
                                          manager=HEAD_DN, title="Менеджер")),
    (f"CN=Gone,OU=Sales,{BASE}", _attrs(GUID["gone"], "gone@example.com", "Уволен", "Продажи", manager=HEAD_DN, uac=514)),
    (f"CN=Newbie,OU=Sales,{BASE}", _attrs(GUID["newbie"], "newbie@example.com", "Новичок", "Продажи", manager=HEAD_DN)),
]


def _directory(entries):
    server = Server("fake-ad", get_info=OFFLINE_AD_2012_R2)
    connection = Connection(server, user="CN=svc,DC=example,DC=local", password="secret", client_strategy=MOCK_SYNC)
    connection.strategy.add_entry("CN=svc,DC=example,DC=local", {"userPassword": "secret", "objectClass": "person"})
    for dn, attrs in entries:
        attrs = {key: value for key, value in attrs.items() if value is not None}
        connection.strategy.add_entry(dn, attrs)
    connection.bind()
    return connection


def _configure(admin, url="ldaps://dc.example.local"):
    response = admin.put("/api/settings/ldap", json={
        "enabled": False, "serverUrl": url, "baseDN": BASE, "bindDN": "CN=svc,DC=example,DC=local",
        "bindPassword": "secret", "searchFilter": "(sAMAccountName={{username}})", "timeoutSeconds": 1,
    })
    assert response.status_code == 200, response.get_json()


@pytest.fixture
def directory(admin, monkeypatch):
    _configure(admin)
    seen = {}

    def connect(config):
        seen["config"] = config
        return _directory(DIRECTORY)

    monkeypatch.setattr(ad_sync, "connect", connect)
    return seen


def test_entries_become_rows_with_heads_and_managers():
    head_dn, seller_dn = f"CN=Head,OU=Sales,{BASE}", f"CN=Seller\\, Jr,OU=Sales,{BASE}"

    def entry(dn, guid, mail, department, manager=None, uac=512):
        return {"dn": dn, "attributes": {"objectGUID": uuid.UUID(guid).bytes_le, "mail": [mail], "displayName": mail,
                                         "sAMAccountName": mail.split("@")[0], "company": "Компания",
                                         "department": department, "manager": manager or [], "userAccountControl": uac}}

    entries = [
        entry(head_dn, "11111111-1111-1111-1111-111111111111", "head@example.com", "Продажи"),
        entry(seller_dn, "22222222-2222-2222-2222-222222222222", "Seller@Example.com", "Продажи", manager=head_dn),
        entry(f"CN=Gone,OU=Sales,{BASE}", "33333333-3333-3333-3333-333333333333", "gone@example.com", "Продажи",
              manager=head_dn, uac=514),
    ]
    config = AdConfig(url="ldaps://dc", bind_dn="", bind_password="", base_dn=BASE)
    rows, refs = entries_to_rows(entries, config)
    head, seller, gone = rows
    assert head.department == ["Компания", "Продажи"] and head.is_head
    assert seller.email == "seller@example.com" and not seller.is_head and seller.login == "Seller"
    assert refs[seller.manager_ref] == head.external_id == "11111111-1111-1111-1111-111111111111"
    assert gone.disabled and not gone.is_head
    ou_rows, _ = entries_to_rows(entries, AdConfig(url="x", bind_dn="", bind_password="", base_dn=BASE, path_source="ou"))
    assert ou_rows[1].department == ["Sales"]
    plain, _ = entries_to_rows(entries, AdConfig(url="x", bind_dn="", bind_password="", base_dn=BASE, detect_heads=False))
    assert not any(row.is_head for row in plain)
    # GUID строкой (так отдаёт ldap3 со схемой каталога) — тот же ключ
    braced = [{"dn": head_dn, "attributes": {"objectGUID": "{11111111-1111-1111-1111-111111111111}", "mail": "h@x.ru"}}]
    assert entries_to_rows(braced, config)[0][0].external_id == "11111111-1111-1111-1111-111111111111"


def test_status_settings_and_unconfigured_directory(app, admin):
    status = admin.get("/api/org/sync/ad").get_json()
    assert status["configured"] is False and status["config"] is None and status["ldap3Available"] is True
    assert status["options"] == {"userFilter": ad_sync.DEFAULT_FILTER, "pathSource": "attributes",
                                 "pathAttributes": ["company", "division", "department"], "detectHeads": True}
    response = admin.post("/api/org/sync/ad", json={})
    assert response.status_code == 503 and response.get_json()["error"] == "ad_unavailable"
    assert "Каталог не настроен" in response.get_json()["message"]

    _configure(admin)
    status = admin.get("/api/org/sync/ad").get_json()
    assert status["configured"] is True and "secret" not in str(status)
    assert status["config"]["url"] == "ldaps://dc.example.local" and status["config"]["baseDn"] == BASE
    assert status["login"] == {"enabled": False, "mode": "local", "url": "ldaps://dc.example.local"}

    saved = admin.put("/api/org/sync/ad/settings", json={"userFilter": "(objectClass=user)", "pathSource": "ou",
                                                         "pathAttributes": [" company ", "department", "company"],
                                                         "detectHeads": False})
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()["options"] == {"userFilter": "(objectClass=user)", "pathSource": "ou",
                                           "pathAttributes": ["company", "department"], "detectHeads": False}
    assert saved.get_json()["config"]["pathSource"] == "ou"
    assert admin.put("/api/org/sync/ad/settings", json={"userFilter": "objectClass=user"}).status_code == 400
    assert admin.put("/api/org/sync/ad/settings", json={"pathAttributes": ["bad attr"]}).status_code == 400
    assert admin.put("/api/org/sync/ad/settings", json={"pathSource": "attributes", "pathAttributes": []}).status_code == 400


def test_unreachable_directory_is_503(app, admin):
    _configure(admin, url="ldap://127.0.0.1:9")
    response = admin.post("/api/org/sync/ad", json={})
    assert response.status_code == 503, response.get_json()
    assert response.get_json()["error"] == "ad_unavailable"
    assert response.get_json()["message"].startswith("Не удалось подключиться к каталогу")


def test_sync_plan_matches_by_guid_and_email_then_applies(app, admin, directory):
    ids = {
        # Найден по objectGUID, хотя почта в каталоге другая
        "head": make_user(app, "head", "editor", name="Head", email="old-head@example.com", org_source="ad",
                          external_id=GUID["head"]),
        "seller": make_user(app, "seller.old", "editor", name="Seller", email="seller@example.com"),
        "gone": make_user(app, "gone", "editor", name="Уволен", email="gone@example.com"),
        # Был в AD, в каталоге больше нет
        "left": make_user(app, "left", "viewer", name="Ушедший", email="left@example.com", org_source="ad",
                          external_id="ffffffff-0000-0000-0000-000000000000"),
    }
    gone_client = app.test_client()
    login(gone_client, "gone")
    plan = admin.post("/api/org/sync/ad", json={"createMissing": True, "deactivateMissing": True})
    assert plan.status_code == 200, plan.get_json()
    body = plan.get_json()
    assert directory["config"].bind_password == "secret" and directory["config"].base_dn == BASE
    assert body["source"] == "ad" and body["canApply"], body
    users = {user["email"]: user for user in body["users"]}
    assert users["head@example.com"]["userId"] == ids["head"]
    assert users["seller@example.com"]["userId"] == ids["seller"]
    assert users["newbie@example.com"]["action"] == "create" and users["ceo@example.com"]["action"] == "create"
    assert {change["field"] for change in users["gone@example.com"]["changes"]} >= {"isActive"}
    assert [item["userId"] for item in body["deactivate"]] == [ids["left"]]
    assert [d["path"] for d in body["departments"]] == ["Компания", "Компания / Продажи"]
    assert body["departments"][0]["kind"] == "company"
    with app.app_context():
        assert db.session.query(Department).count() == 0   # просмотр ничего не пишет

    applied = admin.post("/api/org/sync/ad", json={"createMissing": True, "deactivateMissing": True, "apply": True})
    assert applied.status_code == 200, applied.get_json()
    with app.app_context():
        people = {u.email: u for u in db.session.execute(select(User)).scalars()}
        sales = db.session.execute(select(Department).where(Department.name == "Продажи")).scalar_one()
        assert sales.org_source == "ad"
        head = people["old-head@example.com"]
        assert head.name == "Начальник Продаж" and head.department_id == sales.id
        assert db.session.get(DepartmentHead, (sales.id, head.id)).is_primary   # руководитель найден по подчинённым
        seller = people["seller@example.com"]
        assert seller.org_source == "ad" and seller.external_id == GUID["seller"] and seller.position == "Менеджер"
        newbie = people["newbie@example.com"]
        assert newbie.auth_source == "ldap" and newbie.password_hash is None and newbie.login == "newbie"
        assert newbie.role == "viewer" and newbie.org_source == "ad"
        assert not people["gone@example.com"].is_active and not people["left@example.com"].is_active
        snap = OrgSnapshot()
        ceo = people["ceo@example.com"]
        assert snap.direct_manager(snap.users[seller.id]) == (head.id, "structure")
        # Генеральный директор — в корне без руководства (detectHeads: нет подчинённых в своём подразделении);
        # руководитель продаж подчиняется ему явно
        assert snap.direct_manager(snap.users[head.id]) == (ceo.id, "explicit")
        # Отключённый сотрудник выходит из системы
        assert db.session.execute(select(UserSession).where(UserSession.user_id == ids["gone"])).first() is None
    assert gone_client.get("/api/auth/me").status_code == 401
    # Повторная синхронизация ничего не меняет
    again = admin.post("/api/org/sync/ad", json={"createMissing": True, "deactivateMissing": True}).get_json()
    assert again["summary"]["createDepartments"] == 0 and again["summary"]["createUsers"] == 0
    assert again["summary"]["updateUsers"] == 0 and again["deactivate"] == []


def test_sync_without_create_missing_reports_errors(app, admin, directory):
    response = admin.post("/api/org/sync/ad", json={"apply": True})
    assert response.status_code == 409
    body = response.get_json()
    assert body["error"] == "import_has_errors" and body["message"] == "В данных каталога есть ошибки"
    assert body["summary"]["errors"] == 5


def test_only_admin_syncs(app, admin, directory):
    for role in ("manager", "editor", "viewer"):
        client = new_client(app, f"ad-{role}", role)
        assert client.get("/api/org/sync/ad").status_code == 403
        assert client.post("/api/org/sync/ad", json={}).status_code == 403
        assert client.put("/api/org/sync/ad/settings", json={}).status_code == 403
