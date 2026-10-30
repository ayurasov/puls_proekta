"""Демонстрационные данные «Пульса Проекта»: вымышленная компания ООО «Вектор».

    cd backend
    SQLALCHEMY_DATABASE_URI=sqlite:////tmp/puls-demo.db python ../tools/demo/seed_demo.py

Используется для скриншотов руководств и показа системы. Данные создаются через обычный API
(тестовый клиент Flask): журнал изменений, реестр и снимки выглядят как после настоящей работы.
Сценарий отказывается работать с непустой базой — на production его не запускают.

Все учётные записи получают пароль DEMO_PASSWORD (по умолчанию demo-password-1).
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Профиль e2e: SQLite или PostgreSQL из SQLALCHEMY_DATABASE_URI, без ограничения частоты входов
os.environ.setdefault("FLASK_ENV", "e2e")
BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from sqlalchemy import delete, func, select  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import PlanPresence, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from scripts import migrate_on_start  # noqa: E402

PASSWORD = os.environ.get("DEMO_PASSWORD", "demo-password-1")
# Сегодня по сценарию — даты планов отсчитываются от него, чтобы снимки экранов
# всегда показывали «живой» план: часть работ завершена, часть идёт, часть впереди.
TODAY = date.fromisoformat(os.environ.get("DEMO_TODAY", "2026-10-30"))

USERS = [
    # login, ФИО, должность, роль
    ("sokolova", "Соколова Ирина Викторовна", "Руководитель проектного офиса", "manager"),
    ("morozov", "Морозов Алексей Петрович", "Руководитель проектов", "manager"),
    ("kovaleva", "Ковалёва Мария Андреевна", "Ведущий аналитик", "editor"),
    ("lebedev", "Лебедев Дмитрий Олегович", "Ведущий разработчик", "editor"),
    ("nikitina", "Никитина Ольга Сергеевна", "Инженер по тестированию", "editor"),
    ("volkov", "Волков Сергей Николаевич", "Финансовый контролёр", "viewer"),
    ("egorova", "Егорова Анна Игоревна", "Директор по развитию", "viewer"),
]


def d(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


class Client:
    def __init__(self, app, login):
        self.http = app.test_client()
        self.login = login
        res = self.http.post("/api/auth/login", json={"login": login, "password": PASSWORD})
        assert res.status_code == 200, (login, res.get_json())

    def call(self, method, path, body=None, expect=(200, 201)):
        res = self.http.open("/api" + path, method=method, json=body)
        if res.status_code not in expect:
            raise SystemExit(f"{method} {path} → {res.status_code}: {res.get_json()}")
        return res.get_json()

    def ops(self, plan_id, ops):
        return self.call("POST", f"/plans/{plan_id}/ops", {"ops": ops})


# Цвет полосы по категории — легенда графика показывает категории их цветами
CATEGORY_COLORS = {
    "разработка документации": "teal", "согласование": "purple", "разработка": "blue",
    "тестирование": "indigo", "процессы заказчика": "slate", "отработка замечаний": "orange",
    "подписание": "green", "ожидание оплаты": "amber", "оплата": "amber",
}


def task(key, name, start, end, **fields):
    """Задача; даты зависимых задач заданы так, как их выставил бы автосдвиг по связи (как в 1.0.1:
    FS — со следующего дня после окончания предшественника, у завершённого — после фактической даты)."""
    fields.setdefault("color", CATEGORY_COLORS.get(fields.get("category"), "blue"))
    return {"op": "item.create", "item": {"key": key, "type": "task", "name": name, "start": d(start),
                                          "end": d(end), **fields}}


def milestone(key, name, at, **fields):
    return {"op": "item.create", "item": {"key": key, "type": "milestone", "name": name, "date": d(at), **fields}}


def group(key, name, **fields):
    return {"op": "item.create", "item": {"key": key, "type": "group", "name": name, **fields}}


def assignee(key, name, role, color, user_id=None):
    return {"op": "assignee.create", "assignee": {"key": key, "name": name, "role": role, "color": color,
                                                  "userId": user_id}}


def settings(start, end, scale="week", **display):
    value = {"range": {"start": d(start), "end": d(end)}, "scale": scale}
    if display:
        value["display"] = display
    return {"op": "plan.update", "fields": {"settings": value}}


def portal_main(users):
    """Флагманский план: портал поставщиков, основной договор."""
    return [
        settings(-75, 160, "week", legendEnabled=True, showTaskNamesOnBars=False),
        {"op": "plan.update", "fields": {"subtitle": "Договор № 47-ИТ/2026 · заказчик АО «Меридиан»"}},
        assignee("a_kov", "Ковалёва М. А.", "Аналитик", "teal", users["kovaleva"]),
        assignee("a_leb", "Лебедев Д. О.", "Разработка", "blue", users["lebedev"]),
        assignee("a_nik", "Никитина О. С.", "Тестирование", "purple", users["nikitina"]),
        assignee("a_mor", "Морозов А. П.", "Руководитель проекта", "orange", users["morozov"]),
        assignee("a_cust", "АО «Меридиан»", "Заказчик", "slate"),
        group("g1", "Этап 1. Обследование и техническое задание", color="slate"),
        task("t11", "Интервью с владельцами процессов закупок", -70, -56, parentKey="g1", status="done",
             progress=100, completedDate=d(-57), assigneeKey="a_kov", category="разработка документации"),
        task("t12", "Описание процессов «как есть» и «как будет»", -56, -41, parentKey="g1", status="done",
             progress=100, completedDate=d(-41), assigneeKey="a_kov", category="разработка документации",
             dependencyKey="t11"),
        task("t13", "Техническое задание", -40, -25, parentKey="g1", status="done", progress=100,
             completedDate=d(-22), assigneeKey="a_kov", category="разработка документации", dependencyKey="t12",
             jiraUrl="https://jira.example.com/browse/MER-12"),
        task("t14", "Согласование ТЗ с заказчиком", -21, -12, parentKey="g1", status="done", progress=100,
             completedDate=d(-12), assigneeKey="a_cust", category="согласование", dependencyKey="t13"),
        milestone("m1", "ТЗ утверждено", -12, color="green", lineStyle="solid",
                  comment="Протокол согласования № 3 от заказчика"),
        group("g2", "Этап 2. Разработка", color="blue"),
        task("t21", "Личный кабинет поставщика", -9, 26, parentKey="g2", status="inwork", progress=55,
             assigneeKey="a_leb", category="разработка", dependencyKey="t14", dependencyLag=2),
        task("t22", "Интеграция с ERP заказчика", -5, 40, parentKey="g2", status="blocked", progress=20,
             assigneeKey="a_leb", category="разработка", offset=14, barText="ждём доступ к тестовой ERP"),
        task("t23", "Электронная подпись документов", 5, 35, parentKey="g2", status="inwork", progress=10,
             assigneeKey="a_leb", category="разработка"),
        task("t24", "Отчёты для категорийных менеджеров", 12, 42, parentKey="g2", status="new",
             assigneeKey="a_kov", category="разработка", dependencyKey="t21", dependencyType="SS",
             dependencyLag=21),
        task("t25", "Функциональное тестирование", 27, 61, parentKey="g2", status="new", assigneeKey="a_nik",
             category="тестирование", dependencyKey="t21"),
        milestone("m2", "Приёмка этапа 2", 62, color="blue", lineStyle="dashed"),
        group("g3", "Этап 3. Опытная эксплуатация", color="teal"),
        task("t31", "Обучение пользователей", 63, 77, parentKey="g3", status="new", assigneeKey="a_kov",
             category="процессы заказчика"),
        task("t32", "Опытная эксплуатация", 68, 112, parentKey="g3", status="new", assigneeKey="a_mor",
             category="процессы заказчика", dependencyKey="t25", dependencyLag=6),
        task("t33", "Отработка замечаний", 80, 115, parentKey="g3", status="new", assigneeKey="a_leb",
             category="отработка замечаний"),
        task("t34", "Подписание акта ввода", 119, 128, parentKey="g3", status="new", assigneeKey="a_cust",
             category="подписание", dependencyKey="t32", dependencyLag=6),
        milestone("m3", "Ввод в промышленную эксплуатацию", 131, color="red", lineStyle="solid"),
        task("t41", "Оплата этапа 1", -10, 20, status="inwork", progress=0, assigneeKey="a_cust",
             category="ожидание оплаты"),
        {"op": "note.create", "note": {"key": "n1", "date": d(-5), "text": "Запрошен доступ к тестовой ERP",
                                       "rowKey": "t22", "color": "red", "showDate": True}},
        {"op": "note.create", "note": {"key": "n2", "date": d(40), "text": "Отпуск ведущего разработчика",
                                       "color": "amber", "showDate": False}},
    ]


def portal_addendum(users):
    return [
        settings(60, 330, "month"),
        {"op": "plan.update", "fields": {"subtitle": "Проект дополнительного соглашения на 2027 год"}},
        assignee("a_kov", "Ковалёва М. А.", "Аналитик", "teal", users["kovaleva"]),
        assignee("a_leb", "Лебедев Д. О.", "Разработка", "blue", users["lebedev"]),
        group("g1", "Аукционы и запросы котировок", color="indigo"),
        task("t1", "Требования к модулю торгов", 70, 100, parentKey="g1", assigneeKey="a_kov",
             category="разработка документации"),
        task("t2", "Разработка модуля торгов", 101, 190, parentKey="g1", assigneeKey="a_leb",
             category="разработка", dependencyKey="t1"),
        task("t3", "Аналитика по поставщикам", 150, 240, assigneeKey="a_kov", category="разработка"),
        milestone("m1", "Подписание доп. соглашения", 66, color="green"),
        milestone("m2", "Сдача работ 2027", 300, color="red", lineStyle="solid"),
    ]


def crm(users):
    return [
        settings(-120, 120, "month"),
        assignee("a_mor", "Морозов А. П.", "Руководитель проекта", "orange", users["morozov"]),
        assignee("a_leb", "Лебедев Д. О.", "Разработка", "blue", users["lebedev"]),
        assignee("a_nik", "Никитина О. С.", "Тестирование", "purple", users["nikitina"]),
        task("t1", "Миграция клиентской базы", -110, -60, status="done", progress=100, completedDate=d(-58),
             assigneeKey="a_leb", category="разработка"),
        task("t2", "Настройка воронок продаж", -60, -20, status="done", progress=100, completedDate=d(-25),
             assigneeKey="a_mor", category="разработка"),
        task("t3", "Интеграция с телефонией", -30, 15, status="inwork", progress=70, assigneeKey="a_leb",
             category="разработка"),
        task("t4", "Приёмочные испытания", 16, 40, status="new", assigneeKey="a_nik", category="тестирование",
             dependencyKey="t3"),
        task("t5", "Оплата финального этапа", 41, 70, status="new", assigneeKey="a_mor", category="оплата",
             dependencyKey="t4"),
        milestone("m1", "Запуск в продуктив", 42, color="red", lineStyle="solid"),
    ]


def mobile_app(users):
    return [
        settings(-60, 150, "week"),
        assignee("a_leb", "Лебедев Д. О.", "Разработка", "blue", users["lebedev"]),
        assignee("a_nik", "Никитина О. С.", "Тестирование", "purple", users["nikitina"]),
        assignee("a_kov", "Ковалёва М. А.", "Аналитик", "teal", users["kovaleva"]),
        group("g1", "Релиз 2.0", color="green"),
        task("t1", "Дизайн новых экранов", -50, -20, parentKey="g1", status="done", progress=100,
             completedDate=d(-20), assigneeKey="a_kov", category="разработка"),
        task("t2", "Офлайн-режим", -19, 30, parentKey="g1", status="inwork", progress=40, assigneeKey="a_leb",
             category="разработка", dependencyKey="t1"),
        task("t3", "Push-уведомления", 0, 25, parentKey="g1", status="inwork", progress=15,
             assigneeKey="a_leb", category="разработка"),
        task("t4", "Бета-тестирование", 31, 55, parentKey="g1", status="new", assigneeKey="a_nik",
             category="тестирование", dependencyKey="t2"),
        milestone("m1", "Публикация в магазинах", 60, color="green", lineStyle="solid"),
    ]


def analytics(users):
    return [
        settings(-30, 200, "month"),
        assignee("a_kov", "Ковалёва М. А.", "Аналитик", "teal", users["kovaleva"]),
        task("t1", "Выбор платформы BI", -20, 10, status="inwork", progress=60, assigneeKey="a_kov",
             category="согласование"),
        task("t2", "Пилот на данных продаж", 11, 70, status="new", assigneeKey="a_kov", category="разработка",
             dependencyKey="t1"),
        milestone("m1", "Решение о тиражировании", 75, color="purple"),
    ]


def datacenter(users):
    return [
        settings(-90, 60, "week"),
        assignee("a_leb", "Лебедев Д. О.", "Инфраструктура", "blue", users["lebedev"]),
        task("t1", "Аудит оборудования", -85, -60, status="done", progress=100, completedDate=d(-60),
             assigneeKey="a_leb", category="разработка документации"),
        task("t2", "Перенос тестовых стендов", -59, -30, status="done", progress=100, completedDate=d(-28),
             assigneeKey="a_leb", category="разработка"),
        task("t3", "Перенос продуктивных систем", -10, 12, status="blocked", progress=30, assigneeKey="a_leb",
             category="процессы заказчика", offset=7),
        milestone("m1", "Отключение старого ЦОД", 30, color="red", lineStyle="solid"),
    ]


def main():
    migrate_on_start.main()
    app = create_app("e2e", {"WTF_CSRF_ENABLED": False})
    with app.app_context():
        if db.session.execute(select(func.count()).select_from(User)).scalar_one():
            raise SystemExit("База не пустая — демонстрационные данные создаются только в пустой базе")
        db.session.add(User(login="admin", name="Администратор", role="admin", position="Администратор системы",
                            password_hash=hash_password(PASSWORD)))
        db.session.commit()

    admin = Client(app, "admin")
    users = {}
    for login, name, position, role in USERS:
        created = admin.call("POST", "/users", {"login": login, "name": name, "position": position, "role": role,
                                                "email": f"{login}@vector.example", "password": PASSWORD})
        users[login] = created["id"]

    sokolova = Client(app, "sokolova")
    morozov = Client(app, "morozov")
    directions = {}
    for key, name, color, description in (
        ("impl", "Внедрение у заказчиков", "blue", "Проекты по договорам с внешними заказчиками"),
        ("prod", "Развитие продуктов", "green", "Собственные продукты компании"),
        ("internal", "Внутренние проекты", "purple", "Инфраструктура и процессы ООО «Вектор»"),
    ):
        directions[key] = sokolova.call("POST", "/directions", {"name": name, "color": color,
                                                               "description": description})["id"]

    def project(client, title, direction, subtitle=""):
        created = client.call("POST", "/projects", {"title": title, "subtitle": subtitle,
                                                    "directionId": directions[direction]})
        return created["id"], created["plans"][0]["id"]

    portal, portal_plan = project(morozov, "Портал поставщиков АО «Меридиан»", "impl", "Электронные закупки")
    morozov.call("PATCH", f"/plans/{portal_plan}", {"title": "Основной договор"})
    morozov.ops(portal_plan, portal_main(users))
    addendum = morozov.call("POST", f"/projects/{portal}/plans", {"title": "Доп. соглашение 2027"})["id"]
    morozov.ops(addendum, portal_addendum(users))

    crm_id, crm_plan = project(morozov, "CRM для ТД «Радуга»", "impl", "Внедрение CRM и телефонии")
    morozov.ops(crm_plan, crm(users))
    app_id, app_plan = project(sokolova, "Мобильное приложение «Вектор.Сервис»", "prod", "Релиз 2.0")
    sokolova.ops(app_plan, mobile_app(users))
    bi_id, bi_plan = project(sokolova, "Платформа аналитики", "prod", "Пилот BI")
    sokolova.ops(bi_plan, analytics(users))
    dc_id, dc_plan = project(sokolova, "Переезд в новый ЦОД", "internal", "Инфраструктура")
    sokolova.ops(dc_plan, datacenter(users))

    # Доступы: редакторы — к своим проектам, наблюдатели — ко всему направлению внедрения
    admin.call("POST", "/access", {"userIds": [users["kovaleva"], users["lebedev"], users["nikitina"]],
                                   "projectIds": [portal, crm_id, app_id], "level": "edit"})
    admin.call("POST", "/access", {"userIds": [users["lebedev"]], "projectIds": [dc_id], "level": "edit"})
    admin.call("POST", "/access", {"userIds": [users["kovaleva"]], "projectIds": [bi_id], "level": "edit"})
    admin.call("POST", "/access", {"userIds": [users["volkov"], users["egorova"]],
                                   "directionIds": [directions["impl"], directions["prod"]], "level": "view"})

    # Работа команды: правки, комментарии и сохранённая версия
    morozov.call("POST", f"/plans/{portal_plan}/snapshots", {"label": "Согласованный график"})
    kovaleva = Client(app, "kovaleva")
    lebedev = Client(app, "lebedev")
    lebedev.ops(portal_plan, [{"op": "item.update", "key": "t21", "fields": {"progress": 60}}])
    lebedev.ops(portal_plan, [{"op": "item.update", "key": "t23", "fields": {"status": "inwork", "progress": 15}}])
    kovaleva.ops(portal_plan, [{"op": "item.update", "key": "t24", "fields": {"end": d(52)}}])
    first = lebedev.call("POST", f"/plans/{portal_plan}/items/t22/comments",
                         {"text": "Заказчик обещал выдать доступ к тестовой ERP до конца недели."})
    morozov.call("POST", f"/plans/{portal_plan}/items/t22/comments",
                 {"text": "Напомню на статусе в пятницу. Сдвиг заложили, приёмку этапа не двигаем.",
                  "replyToId": first["comment"]["id"]})
    kovaleva.call("POST", f"/plans/{portal_plan}/items/t13/comments",
                  {"text": "Финальная версия ТЗ — в папке проекта, раздел «Документы»."})

    # Архив и корзина: первоначальный график сохранён в архиве, черновик и отменённый проект удалены
    initial = morozov.call("POST", f"/projects/{portal}/plans",
                           {"title": "Первоначальный график", "sourcePlanId": portal_plan})["id"]
    morozov.call("POST", f"/plans/{initial}/archive")
    draft = morozov.call("POST", f"/projects/{crm_id}/plans", {"title": "Черновик графика"})["id"]
    morozov.call("POST", f"/plans/{draft}/archive")
    morozov.call("DELETE", f"/plans/{draft}")
    cancelled, _ = project(sokolova, "Пилот голосового помощника", "prod", "Проект отменён")
    admin.call("DELETE", f"/projects/{cancelled}")

    # Правки через API отмечают авторов «сейчас на плане» — на демо-стенде никого нет
    with app.app_context():
        db.session.execute(delete(PlanPresence))
        db.session.commit()

    print(f"Демо-данные созданы: {len(USERS) + 1} сотрудников, 3 направления, 5 проектов. Пароль: {PASSWORD}")


if __name__ == "__main__":
    main()
