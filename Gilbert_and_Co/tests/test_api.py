"""The validation scenario of the milestone, through the API the interface uses:
a new user imports a planning and gets a result, with no manual technical step."""
import json
import time

import pytest


def test_health(client):
    assert client.get("/api/health").json()["ok"]


def test_import_refuses_incomplete_or_wrong_files(client, docs):
    r = client.post("/api/imports", files={"schedule": ("p.csv", (docs / "released_schedule.csv").read_bytes())})
    assert r.status_code == 400 and "distances" in r.json()["detail"]
    r = client.post("/api/imports", files={"schedule": ("p.csv", b"a,b\n1,2\n"), "distances": ("d.csv", (docs / "distances.csv").read_bytes())})
    assert r.status_code == 400 and "colonnes manquantes" in r.json()["detail"]
    r = client.post("/api/imports", files={"schedule": ("p.txt", b"x"), "distances": ("d.csv", b"x")})
    assert r.status_code == 400


def test_situation_parameters_are_checked(client):
    imp = client.post("/api/imports", data={"example": "true"}).json()
    assert client.get(f"/api/imports/{imp['id']}/situation", params={"weight": "nope"}).status_code == 422
    s = client.get(f"/api/imports/{imp['id']}/situation", params={"weight": "median"}).json()
    assert s["params"]["weight"] == "median" and s["kpi"]["cost"] < 176_724


@pytest.mark.slow
def test_new_user_imports_and_gets_a_result(client, docs):
    files = {"schedule": ("mon_planning.csv", (docs / "released_schedule.csv").read_bytes()),
             "distances": ("distances.csv", (docs / "distances.csv").read_bytes())}
    imp = client.post("/api/imports", files=files)
    assert imp.status_code == 201
    imp = imp.json()
    assert imp["label"] == "mon_planning" and imp["first"] == "2026-11-09"

    run = client.post("/api/runs", data={"settings": json.dumps({"label": "Validation", "import_id": imp["id"]})})
    assert run.status_code == 202
    rid = run.json()["id"]
    deadline = time.time() + 300
    while client.get(f"/api/runs/{rid}").json()["status"] in ("en_attente", "en_cours"):
        assert time.time() < deadline, "calcul trop long"
        time.sleep(2)
    assert client.get(f"/api/runs/{rid}").json()["status"] == "termine"

    sm = client.get(f"/api/runs/{rid}/summary").json()
    k = sm["kpi"]
    assert k["saving"] >= 40_000 and k["trucks_after"] < 800 and k["runtime_s"] < 90      # the three targets
    assert all(c["ok"] for c in sm["controls"])                                             # every rule on the final plan
    sit = client.get(f"/api/runs/{rid}/situation").json()
    assert sit["kpi"]["trucks"] == k["trucks_before"]
    for name in ("resultats_consolidation.xlsx", "sortie1_profils.csv", "sortie2_camions.csv", "sortie3_saving.csv", "gilbert_resultats.zip"):
        assert client.get(f"/api/runs/{rid}/files/{name}").status_code == 200
    assert client.get(f"/api/imports/{imp['id']}").json()["runs"] == 1
