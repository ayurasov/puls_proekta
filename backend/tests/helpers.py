def create_project(client, title="Проект", direction_id=None):
    response = client.post("/api/projects", json={"title": title, "directionId": direction_id})
    assert response.status_code == 201, response.get_json()
    project = response.get_json()
    return project["id"], project["plans"][0]["id"]


def ops(client, plan_id, operations, base=None):
    response = client.post(f"/api/plans/{plan_id}/ops", json={"baseRevision": base, "ops": operations})
    return response


def ok_ops(client, plan_id, operations, base=None):
    response = ops(client, plan_id, operations, base)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def task(key, name="Задача", start="2026-05-01", end="2026-05-05", **extra):
    return {"op": "item.create", "item": {"key": key, "type": "task", "name": name, "start": start, "end": end, **extra}}
