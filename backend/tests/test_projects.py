from __future__ import annotations

from tests.conftest import UserClient, make_user


def _project(c, area_id, name="Plataforma"):
    r = c.post("/api/projects", json={"area_id": area_id, "name": name, "description": "Descripción"})
    assert r.status_code == 200, r.text
    return r.json()


def test_project_task_lifecycle_and_activity(partners):
    c1, c2, _ = partners["clients"]
    p = _project(c1, partners["general"])
    t = c1.post(f"/api/projects/{p['id']}/tasks", json={"title": "Diseñar API", "assignee_id": c2.id}).json()
    sub = c1.post(f"/api/projects/{p['id']}/tasks", json={"title": "Endpoints", "parent_id": t["id"]}).json()
    assert sub["parent_id"] == t["id"]
    # El asignado recibe una notificación real
    notes = c2.get("/api/notifications").json()
    assert notes["unread"] == 1 and "Diseñar API" in notes["items"][0]["title"]
    # La oficina solo expone a cada socio su propio número de avisos sin leer
    me = next(x for x in c2.get("/api/office/state").json()["people"] if x["id"] == c2.id)
    other = next(x for x in c1.get("/api/office/state").json()["people"] if x["id"] == c2.id)
    assert me["unread_notifications"] == 1 and other["unread_notifications"] == 0
    started = c2.patch(f"/api/tasks/{t['id']}", json={"version": t["version"], "status": "in_progress"}).json()
    assert started["started_at"] and started["version"] == 2
    # Concurrencia optimista: versión antigua -> 409
    stale = c1.patch(f"/api/tasks/{t['id']}", json={"version": 1, "title": "Otro"})
    assert stale.status_code == 409
    done = c2.patch(f"/api/tasks/{t['id']}", json={"version": 2, "status": "done"}).json()
    assert done["completed_at"]
    kinds = [a["kind"] for a in c1.get(f"/api/activity?project_id={p['id']}").json()]
    assert {"project.created", "task.created", "task.started", "task.completed"} <= set(kinds)
    # La oficina muestra el resumen de trabajo del socio
    state = c1.get("/api/office/state").json()
    person = next(x for x in state["people"] if x["id"] == c2.id)
    assert person["work_summary"]["done_last_7_days"] == 1
    assert person["last_activity"]["kind"] == "task.completed"


def test_viewer_cannot_modify(app, partners):
    c1, _, _ = partners["clients"]
    make_user("lector")
    lector = UserClient(app, "lector")
    area = c1.post("/api/areas", json={"name": "Lectura"}).json()
    c1.put(f"/api/areas/{area['id']}/members/{lector.id}", json={"role": "viewer"})
    p = _project(c1, area["id"])
    assert lector.get(f"/api/projects/{p['id']}").status_code == 200
    assert lector.post(f"/api/projects/{p['id']}/tasks", json={"title": "x"}).status_code == 403
    assert lector.post("/api/projects", json={"area_id": area["id"], "name": "Nuevo"}).status_code == 403
    # Un viewer sí puede comentar
    assert (
        lector.post(
            "/api/comments", json={"target_type": "project", "target_id": p["id"], "body": "Buena idea"}
        ).status_code
        == 200
    )


def test_non_member_isolated(app, partners):
    c1, _, _ = partners["clients"]
    make_user("ajeno")
    ajeno = UserClient(app, "ajeno")
    p = _project(c1, partners["general"])
    assert ajeno.get(f"/api/projects/{p['id']}").status_code == 404
    assert ajeno.get("/api/projects").json() == []
    assert ajeno.get("/api/activity").json() == []
    assert ajeno.get(f"/api/comments?target_type=project&target_id={p['id']}").status_code == 404


def test_note_autosave_merge_and_conflict(partners):
    c1, c2, _ = partners["clients"]
    gid = partners["general"]
    note = c1.post(
        "/api/notes",
        json={"area_id": gid, "title": "Especificación", "kind": "spec", "content": "uno\ndos\ntres\ncuatro\ncinco\n"},
    ).json()
    assert note["version"] == 1
    # Dos socios editan desde la versión 1 en líneas distintas: fusión automática
    a = c1.put(
        f"/api/notes/{note['id']}",
        json={"base_version": 1, "title": "Especificación", "content": "UNO\ndos\ntres\ncuatro\ncinco\n"},
    )
    assert a.status_code == 200 and a.json()["version"] == 2
    b = c2.put(
        f"/api/notes/{note['id']}",
        json={"base_version": 1, "title": "Especificación", "content": "uno\ndos\ntres\ncuatro\nCINCO\n"},
    )
    assert b.status_code == 200, b.text
    assert b.json()["merged"] is True and b.json()["content"] == "UNO\ndos\ntres\ncuatro\nCINCO\n"
    # Cambios solapados: 409 con ambas versiones y marcadores; nada se pierde
    c = c1.put(
        f"/api/notes/{note['id']}",
        json={"base_version": 3, "title": "Especificación", "content": "UNO\nDOS-A\ntres\ncuatro\nCINCO\n"},
    )
    assert c.status_code == 200
    d = c2.put(
        f"/api/notes/{note['id']}",
        json={"base_version": 3, "title": "Especificación", "content": "UNO\nDOS-B\ntres\ncuatro\nCINCO\n"},
    )
    assert d.status_code == 409
    detail = d.json()["detail"]
    assert "DOS-A" in detail["current"]["content"]
    assert "DOS-B" in detail["merged_with_markers"] and "<<<<<<<" in detail["merged_with_markers"]
    # El usuario resuelve y confirma explícitamente
    e = c2.put(
        f"/api/notes/{note['id']}",
        json={"base_version": 4, "title": "Especificación", "content": "UNO\nDOS-A y B\ntres\ncuatro\nCINCO\n"},
    )
    assert e.status_code == 200 and e.json()["version"] == 5
    revs = c1.get(f"/api/notes/{note['id']}/revisions").json()
    assert [r["version"] for r in revs] == [5, 4, 3, 2, 1]
    old = c1.get(f"/api/notes/{note['id']}/revisions/1").json()
    assert old["content"].startswith("uno")


def test_decisions_require_owner_to_approve(app, partners):
    c1, _, _ = partners["clients"]
    make_user("editor1")
    ed = UserClient(app, "editor1")
    area = c1.post("/api/areas", json={"name": "Decisiones"}).json()
    c1.put(f"/api/areas/{area['id']}/members/{ed.id}", json={"role": "editor"})
    d = ed.post(
        "/api/decisions", json={"area_id": area["id"], "title": "Usar PostgreSQL", "body": "Por FTS y fiabilidad"}
    ).json()
    assert d["status"] == "proposed"
    assert ed.post(f"/api/decisions/{d['id']}/status", json={"status": "approved"}).status_code == 403
    ok = c1.post(f"/api/decisions/{d['id']}/status", json={"status": "approved"}).json()
    assert ok["status"] == "approved" and ok["decided_by"] == c1.id


def test_project_summary_uses_only_recorded_events(partners):
    c1, c2, _ = partners["clients"]
    p = _project(c1, partners["general"], "Resumen")
    empty = c1.post(f"/api/projects/{p['id']}/summary").json()
    assert empty["events"] >= 1  # solo la creación del proyecto
    t = c2.post(f"/api/projects/{p['id']}/tasks", json={"title": "Tarea real"}).json()
    c2.patch(f"/api/tasks/{t['id']}", json={"version": 1, "status": "blocked"})
    s = c1.post(f"/api/projects/{p['id']}/summary").json()
    assert s["counts"]["tasks_created"] == 1 and s["counts"]["errors"] == 1
    assert "Tarea real" in s["markdown"] and "Socio2" in s["markdown"]
    detail = c1.get(f"/api/projects/{p['id']}").json()
    assert detail["last_summary"] == s["markdown"]
    # Sin actividad nueva, el siguiente resumen lo indica en vez de inventar
    again = c1.post(f"/api/projects/{p['id']}/summary").json()
    assert again["events"] == 0 and "No se registró actividad" in again["markdown"]


def test_comments_flow(partners):
    c1, c2, _ = partners["clients"]
    p = _project(c1, partners["general"])
    c2.post("/api/comments", json={"target_type": "project", "target_id": p["id"], "body": "Hola"})
    comments = c1.get(f"/api/comments?target_type=project&target_id={p['id']}").json()
    assert comments[0]["body"] == "Hola" and comments[0]["author"]["id"] == c2.id
    # El autor puede borrar; otro socio propietario del área también
    assert c1.delete(f"/api/comments/{comments[0]['id']}").status_code == 200
