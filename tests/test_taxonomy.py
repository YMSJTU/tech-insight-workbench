from backend.db import Session, Paper, PaperCategories, init_db


def create(client, name, parent=None, dimension="technology"):
    version = client.get("/api/taxonomy").json()["version"]
    return client.post(
        "/api/taxonomy",
        json={
            "name": name,
            "dimension": dimension,
            "parent_id": parent,
            "expected_version": version,
        },
    )


def edit(client, id_, name, parent=None, dimension="technology", version=None):
    return client.put(
        "/api/taxonomy/" + id_,
        json={
            "name": name,
            "parent_id": parent,
            "dimension": dimension,
            "expected_version": version
            or client.get("/api/taxonomy").json()["version"],
        },
    )


def paper():
    with Session.begin() as s:
        p = Paper(identity="manual:test", title="Manual classification test")
        s.add(p)
        s.flush()
        return p.id


def test_deep_tree_move_and_rename(client):
    a = create(client, "A", "cache").json()["node"]["id"]
    b = create(client, "B", a).json()["node"]["id"]
    c = create(client, "C", b).json()["node"]["id"]
    assert edit(client, a, "Renamed", "compiler").status_code == 200
    nodes = {n["id"]: n for n in client.get("/api/taxonomy").json()["nodes"]}
    assert nodes[a]["name"] == "Renamed" and nodes[a]["parent_id"] == "compiler"
    assert nodes[c]["parent_id"] == b and nodes[b]["parent_id"] == a
    init_db()
    assert (
        next(n for n in client.get("/api/taxonomy").json()["nodes"] if n["id"] == a)[
            "parent_id"
        ]
        == "compiler"
    )


def test_cycles_cross_dimension_duplicates_and_blank_names(client):
    assert edit(client, "arch", "体系结构", "prefetch").status_code == 400
    assert edit(client, "cache", "Cache 系统", "cache").status_code == 400
    assert edit(client, "cache", "Cache 系统", "performance").status_code == 400
    assert create(client, "Cache 预取", "cache").status_code == 409
    assert create(client, "   ").status_code == 400
    assert edit(client, "cache", "Cache 系统", None, "problem").status_code == 400


def test_optimistic_taxonomy_conflict(client):
    version = client.get("/api/taxonomy").json()["version"]
    create(client, "new")
    assert edit(client, "cache", "new name", "arch", version=version).status_code == 409


def test_delete_empty_seed_not_resurrected(client):
    version = client.get("/api/taxonomy").json()["version"]
    assert (
        client.delete(
            "/api/taxonomy/core", params={"expected_version": version}
        ).status_code
        == 200
    )
    init_db()
    assert "core" not in [n["id"] for n in client.get("/api/taxonomy").json()["nodes"]]
    version = client.get("/api/taxonomy").json()["version"]
    assert (
        client.delete(
            "/api/taxonomy/cache", params={"expected_version": version}
        ).status_code
        == 409
    )


def test_manual_categories_query_move_reset_and_conflict(client):
    id_ = paper()
    body = {
        "node_ids": ["prefetch", "bandwidth"],
        "expected_revision": 0,
        "expected_taxonomy_version": 1,
    }
    assert (
        client.put("/api/papers/" + id_ + "/categories", json=body).status_code == 200
    )
    assert (
        client.get("/api/papers?technology=arch&problem=performance").json()["total"]
        == 1
    )
    assert (
        client.put("/api/papers/" + id_ + "/categories", json=body).status_code == 409
    )
    assert client.get("/api/papers/" + id_).json()["category_manual"] is True
    assert edit(client, "prefetch", "预取策略", "compiler").status_code == 200
    assert client.get("/api/papers?technology=arch").json()["total"] == 0
    assert (
        client.get("/api/papers?technology=compiler&problem=performance").json()[
            "total"
        ]
        == 1
    )
    version = client.get("/api/taxonomy").json()["version"]
    assert (
        client.delete(
            "/api/taxonomy/prefetch", params={"expected_version": version}
        ).status_code
        == 409
    )
    body.update(
        expected_revision=1,
        expected_taxonomy_version=version,
        manual=False,
        node_ids=[],
    )
    assert (
        client.put("/api/papers/" + id_ + "/categories", json=body).status_code == 200
    )
    assert client.get("/api/papers/" + id_).json()["category_manual"] is False
    assert client.get("/api/papers?technology=compiler").json()["total"] == 0


def test_manual_empty_categories_remain_explicit(client):
    id_ = paper()
    assert (
        client.put(
            "/api/papers/" + id_ + "/categories",
            json={
                "node_ids": [],
                "expected_revision": 0,
                "expected_taxonomy_version": 1,
            },
        ).status_code
        == 200
    )
    with Session() as s:
        assert s.get(PaperCategories, id_).manual is True
    assert client.get("/api/papers/" + id_).json()["effective_categories"] == []
