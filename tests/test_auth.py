"""
Access control.

What this file is defending
--------------------------
Before this existed, `GET /api/patients` returned every patient in the database
to anyone who could reach the URL. These tests exist so that cannot come back
quietly.

The properties, in the order they matter
---------------------------------------
1. **No route that reads a patient works without a session.** Tested by
   enumerating them, not by listing them — a new unprotected endpoint should fail
   this file without anyone remembering to add a case.
2. **Authentication is not authorisation.** A signed-in clinician from hospital A
   must not see hospital B's patients, and must get 404 rather than 403 — a 403
   confirms the record exists, which is a patient-existence oracle.
3. **Carers do not log in, and their token is as narrow as a credential gets.**
   One check-in. Not the patient, not the list, not another check-in.
4. **There is no flag that disables authentication.** Bootstrap closes behind
   itself and cannot be reopened.
"""

import pytest
from fastapi.testclient import TestClient

from api.auth import MIN_PASSWORD_LENGTH

GOOD_PASSWORD = "a-long-enough-passphrase"


@pytest.fixture
def client():
    from api.database import Base, engine
    from api.main import app

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return TestClient(app)


@pytest.fixture
def db():
    from api.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _signup(client, email="lead@hospital-a.test", org="Hospital A", code=""):
    r = client.post("/api/auth/signup", json={
        "email": email, "password": GOOD_PASSWORD, "organisation": org,
        "code": code})
    assert r.status_code == 200, r.text
    return r.json()


def _second_org_client(client_factory, db):
    """A second signed-in clinician in a DIFFERENT organisation.

    Built by creating the org and user directly, because no API route creates a
    user in another organisation — which is itself the point.
    """
    from api.auth import create_user
    from api.database import SessionLocal
    from api.main import app

    with SessionLocal() as s:
        create_user(s, email="lead@hospital-b.test", password=GOOD_PASSWORD,
                    organisation="Hospital B")
    other = TestClient(app)
    r = other.post("/api/auth/login", json={
        "email": "lead@hospital-b.test", "password": GOOD_PASSWORD})
    assert r.status_code == 200
    return other


def _make_patient(client, ref="ward3-014", checkins=2):
    """A patient and some check-ins, inserted directly.

    Deliberately NOT via `POST /api/assess`. Access control has nothing to do with
    the risk models, and routing these tests through the predictor would make them
    fail wherever the pickles cannot load — which is a different problem wearing
    this file's clothes.
    """
    from datetime import timedelta

    from api.database import CheckIn, Patient, SessionLocal, utcnow

    org_id = client.get("/api/auth/me").json()["organisation_id"]
    with SessionLocal() as s:
        p = Patient(organisation_id=org_id, patient_ref=ref,
                    caregiver_contact="+919999999999", consent_recorded=True)
        s.add(p)
        s.commit()
        s.refresh(p)
        ids = []
        for day in range(1, checkins + 1):
            c = CheckIn(patient_id=p.id, reason=f"Day {day * 3} check-in",
                        scheduled_for=utcnow() + timedelta(days=day))
            s.add(c)
            s.commit()
            s.refresh(c)
            ids.append(c.id)
        return {"patient_id": p.id, "check_in_ids": ids}


# ------------------------------------------------------------------- bootstrap
def test_signup_creates_an_account_and_signs_in(client):
    body = _signup(client)
    assert body["organisation_id"]
    assert client.get("/api/auth/me").status_code == 200


def test_anyone_can_register_not_just_the_first_person(client):
    """The bug this replaced: exactly ONE account could ever exist.

    Anyone handed the app saw a password box with no way to obtain a password. A
    login form that offers no route to obtaining a login is not a security
    measure — it is a dead end that looks like one.
    """
    _signup(client)

    from api.main import app
    second = TestClient(app)
    r = second.post("/api/auth/signup", json={
        "email": "reviewer@hospital-b.test", "password": GOOD_PASSWORD,
        "organisation": "Reviewer"})
    assert r.status_code == 200, r.text
    assert second.get("/api/auth/me").status_code == 200


def test_auth_status_leaks_nothing(client):
    """The sign-in screen needs two facts before anyone signs in: whether to
    offer registration, and whether to show a code field. That is all it may
    learn — no user count, no email, no organisation name. An unauthenticated
    endpoint that names the hospital using the software has told a stranger
    something they should have had to earn."""
    body = client.get("/api/auth/status").json()
    assert set(body) == {"signup_open", "signup_code_required"}
    assert body["signup_open"] is True


def test_a_registration_code_gates_signup_when_set(client, monkeypatch):
    """Off by default, because a reviewer must be able to register unaided. This
    exists for the moment a public deployment makes open registration a problem.
    Named honestly as a speed bump: it stops casual signups, not anyone
    determined."""
    monkeypatch.setenv("RECOVERYLENS_SIGNUP_CODE", "stroke-2026")

    assert client.get("/api/auth/status").json()["signup_code_required"] is True

    refused = client.post("/api/auth/signup", json={
        "email": "a@b.test", "password": GOOD_PASSWORD, "organisation": "X"})
    assert refused.status_code == 403

    wrong = client.post("/api/auth/signup", json={
        "email": "a@b.test", "password": GOOD_PASSWORD, "organisation": "X",
        "code": "guess"})
    assert wrong.status_code == 403
    assert wrong.json()["detail"] == refused.json()["detail"], \
        "a wrong code and a missing code must be indistinguishable"

    ok = client.post("/api/auth/signup", json={
        "email": "a@b.test", "password": GOOD_PASSWORD, "organisation": "X",
        "code": "stroke-2026"})
    assert ok.status_code == 200


def test_the_same_email_cannot_register_twice(client):
    _signup(client)
    from api.main import app
    again = TestClient(app).post("/api/auth/signup", json={
        "email": "lead@hospital-a.test", "password": GOOD_PASSWORD,
        "organisation": "Impostor"})
    assert again.status_code == 409


def test_short_passwords_are_refused(client):
    r = client.post("/api/auth/signup", json={
        "email": "a@b.test", "password": "x" * (MIN_PASSWORD_LENGTH - 1),
        "organisation": "X"})
    assert r.status_code == 400


# ----------------------------------------------------------------------- login
def test_wrong_email_and_wrong_password_are_indistinguishable(client):
    """Distinguishing them turns the login form into a way to discover which
    clinicians have accounts at a named hospital."""
    _signup(client)
    from api.main import app

    fresh = TestClient(app)
    wrong_user = fresh.post("/api/auth/login", json={
        "email": "nobody@nowhere.test", "password": GOOD_PASSWORD})
    wrong_pass = fresh.post("/api/auth/login", json={
        "email": "lead@hospital-a.test", "password": "definitely-wrong-passphrase"})

    assert wrong_user.status_code == wrong_pass.status_code == 401
    assert wrong_user.json()["detail"] == wrong_pass.json()["detail"]


def test_password_is_not_stored_in_plaintext(client, db):
    from api.database import User

    _signup(client)
    user = db.query(User).first()
    assert GOOD_PASSWORD not in user.password_hash
    assert user.password_hash.startswith("scrypt$")


def test_logout_revokes_server_side_not_just_the_cookie(client):
    """Deleting the cookie alone would leave a valid token in anything that copied
    it. This is the reason sessions are a table and not a JWT."""
    _signup(client)
    token = client.cookies.get("rl_session")
    assert token

    client.post("/api/auth/logout")

    # Re-present the exact token that was just revoked.
    from api.main import app
    replay = TestClient(app)
    replay.cookies.set("rl_session", token)
    assert replay.get("/api/auth/me").status_code == 401


# --------------------------------------------------- every patient route is shut
PROTECTED = [
    ("get", "/api/patients"),
    ("get", "/api/patients/1"),
    ("delete", "/api/patients/1"),
    ("get", "/api/checkins/due"),
    ("get", "/api/escalations"),
    ("get", "/api/auth/me"),
    ("get", "/api/checkins/1/link"),
    ("post", "/api/assess"),
    ("post", "/api/patients/1/assess"),
    ("post", "/api/checkins/1/send"),
    ("post", "/api/auth/invite"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_no_patient_route_answers_without_a_session(client, method, path):
    # 401 must come BEFORE body validation. If a route validated its payload
    # first, an anonymous caller would get 422 and learn the schema of an endpoint
    # they cannot use — and, worse, a malformed-body 422 could be mistaken for
    # "this endpoint is protected" when it is not.
    kwargs = {"json": {}} if method == "post" else {}
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code in (401, 403), f"{method.upper()} {path} -> {r.status_code}"


def test_every_patient_route_is_in_the_protected_list(client):
    """Guards against the real failure mode: someone adds an endpoint that reads
    patient data and forgets to protect it.

    Enumerates the live route table rather than trusting the list above, so a new
    unprotected route fails here without anyone remembering to add a case.
    """
    from api.main import app

    allowed_open = {
        "/health", "/api/auth/status", "/api/auth/login", "/api/auth/signup",
        "/api/auth/logout",
        # Signature-validated instead of session-authenticated. Twilio cannot
        # hold a cookie; it signs every request and we verify it.
        "/api/webhooks/twilio",
        # The carer's own token IS the credential on these three.
        "/api/checkins/by-token",
        "/api/checkins/{checkin_id}/respond",
        "/api/checkins/{checkin_id}/voice",
        "/api/checkins/{checkin_id}/voice/confirm",
        # Public by necessity: Twilio fetches the audio itself and cannot hold a
        # cookie. Narrowed instead — 256-bit token as the credential, 15-minute
        # expiry, no listing route, and the patient reference stripped before
        # synthesis so a leaked URL yields generic guidance. See api/media.py.
        "/media/{token}",
        # Guidance is published guideline text with no patient data in it.
        "/api/guidance", "/api/guidance/{trigger}", "/api/guidance/resolve",
        "/api/guidance/ask",
        # Corpus and model metadata, no patient data.
        "/api/meta/schema", "/api/meta/metrics", "/api/triage/status",
        "/docs", "/openapi.json", "/docs/oauth2-redirect", "/redoc",
    }

    import inspect

    from conftest import iter_api_routes

    seen, unguarded = 0, []
    for path, endpoint in iter_api_routes(app):
        if path in allowed_open or not path.startswith(("/api", "/health")):
            continue
        seen += 1
        try:
            source = inspect.getsource(endpoint)
        except Exception:
            source = ""
        if "current_user" not in source:
            unguarded.append(path)

    # The walk itself has to be verified. `app.routes` does not contain routes
    # from included routers flat — this FastAPI version wraps them in
    # `_IncludedRouter` — so an earlier version of this test enumerated only
    # main.py and skipped the entire webhook and voice surface while appearing to
    # check everything. A guard that silently checks nothing is worse than none.
    assert seen > 10, (
        f"only {seen} routes were examined, which means the walk is not reaching "
        f"included routers again. See iter_api_routes in conftest.py.")

    assert not unguarded, (
        f"these routes read the API without requiring a session: {unguarded}. "
        f"Either add Depends(current_user) or add the path to allowed_open with "
        f"a comment saying why it is safe.")


# --------------------------------------------------------- org scoping is real
def test_a_clinician_cannot_see_another_organisations_patients(client, db):
    _signup(client)
    _make_patient(client)
    assert len(client.get("/api/patients").json()) == 1

    other = _second_org_client(TestClient, db)
    assert other.get("/api/patients").json() == []


def test_cross_org_access_returns_404_not_403(client, db):
    """A 403 would confirm the patient exists elsewhere — an existence oracle an
    attacker could use to enumerate ids and learn which hospital holds which
    record. "Not yours" and "not there" must be the same answer."""
    _signup(client)
    pid = _make_patient(client)["patient_id"]

    other = _second_org_client(TestClient, db)
    assert other.get(f"/api/patients/{pid}").status_code == 404
    assert other.delete(f"/api/patients/{pid}").status_code == 404
    assert other.get(f"/api/patients/{pid + 999}").status_code == 404


def test_the_same_patient_ref_in_two_orgs_does_not_collide(client, db):
    """Two hospitals both using "ward3-014" must not be merged into one record.

    Asserted on the scoped lookup `/api/assess` performs, rather than by calling
    it — the collision is a scoping question, not a prediction one.
    """
    from api.auth import scoped_patients
    from api.database import Patient, SessionLocal, User

    _signup(client)
    mine = _make_patient(client, ref="ward3-014")["patient_id"]

    other = _second_org_client(TestClient, db)
    other_org = other.get("/api/auth/me").json()["organisation_id"]

    with SessionLocal() as s:
        theirs = Patient(organisation_id=other_org, patient_ref="ward3-014")
        s.add(theirs)
        s.commit()

        user_b = s.query(User).filter(User.email == "lead@hospital-b.test").one()
        found = (scoped_patients(user_b, s)
                 .filter(Patient.patient_ref == "ward3-014").all())
        assert len(found) == 1
        assert found[0].id != mine, "the lookup crossed an organisation boundary"


def test_escalations_and_due_checkins_are_scoped(client, db):
    _signup(client)
    _make_patient(client)
    assert len(client.get("/api/checkins/due?include_scheduled=true").json()) > 0

    other = _second_org_client(TestClient, db)
    assert other.get("/api/checkins/due?include_scheduled=true").json() == []
    assert other.get("/api/escalations").json() == []


def test_invite_cannot_place_a_user_in_another_organisation(client, db):
    """There is no route that does this. The test records the absence."""
    _signup(client)
    r = client.post("/api/auth/invite", json={
        "email": "colleague@hospital-a.test", "password": GOOD_PASSWORD})
    assert r.status_code == 200
    assert r.json()["organisation_id"] == client.get(
        "/api/auth/me").json()["organisation_id"]


def test_two_people_who_sign_up_cannot_see_each_others_patients(client, db):
    """The privacy claim the sign-in screen makes, tested through the exact path a
    reviewer would take: two strangers each register themselves.

    The other scoping tests build the second organisation directly, because no API
    route creates a user in someone else's org. This one goes through `/signup`
    twice, which is what actually happens when you hand the app to a reviewer and
    a judge and each makes an account.
    """
    from api.main import app

    _signup(client, email="akash@test.local", org="Akash")
    mine = _make_patient(client, ref="my-patient")["patient_id"]

    reviewer = TestClient(app)
    reviewer.post("/api/auth/signup", json={
        "email": "reviewer@test.local", "password": GOOD_PASSWORD,
        "organisation": "Reviewer"})
    theirs = _make_patient(reviewer, ref="their-patient")["patient_id"]

    assert [p["patient_ref"] for p in client.get("/api/patients").json()] \
        == ["my-patient"]
    assert [p["patient_ref"] for p in reviewer.get("/api/patients").json()] \
        == ["their-patient"]

    # Not merely absent from the list — unreachable by direct id, and 404 rather
    # than 403 so the id cannot be used to confirm the record exists.
    assert reviewer.get(f"/api/patients/{mine}").status_code == 404
    assert client.get(f"/api/patients/{theirs}").status_code == 404
    assert reviewer.get("/api/escalations").json() == []


def test_signing_up_without_an_organisation_still_gets_a_private_workspace(client):
    """Most people signing up are one person trying the product, not a hospital.
    Leaving the field blank must not produce a shared or nameless space."""
    r = client.post("/api/auth/signup", json={
        "email": "solo@test.local", "password": GOOD_PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["organisation_id"]
    assert "solo@test.local" in body["organisation"]


# ------------------------------------------------------ the carer's credential
def test_a_carer_reaches_one_checkin_with_a_token_and_no_login(client, db):
    _signup(client)
    checkin_id = _make_patient(client)["check_in_ids"][0]
    token = client.get(f"/api/checkins/{checkin_id}/link").json()["token"]

    from api.main import app
    carer = TestClient(app)          # no session at all
    got = carer.get(f"/api/checkins/by-token?token={token}")
    assert got.status_code == 200
    assert got.json()["id"] == checkin_id

    answered = carer.post(f"/api/checkins/{checkin_id}/respond?token={token}", json={
        "taking_medication": True, "new_symptoms": False,
        "worse_than_last_week": False, "free_text": "all fine"})
    assert answered.status_code == 200


def test_a_carer_token_grants_nothing_else(client, db):
    """As narrow as a credential gets: one check-in. Not the patient, not the
    list, not another check-in."""
    _signup(client)
    first, second = _make_patient(client)["check_in_ids"]
    token = client.get(f"/api/checkins/{first}/link").json()["token"]

    from api.main import app
    carer = TestClient(app)
    assert carer.get("/api/patients").status_code == 401
    assert carer.get("/api/escalations").status_code == 401
    assert carer.get("/api/checkins/due").status_code == 401

    wrong = carer.post(f"/api/checkins/{second}/respond?token={token}", json={
        "taking_medication": True, "new_symptoms": False,
        "worse_than_last_week": False})
    assert wrong.status_code == 403, "a token for one check-in must not open another"


def test_a_bad_token_is_rejected(client):
    _signup(client)
    assert client.get("/api/checkins/by-token?token=not-a-real-token"
                      ).status_code == 404


def test_an_answered_checkin_stops_accepting_its_token(client, db):
    """The carer's credential dies with the thing it was for."""
    _signup(client)
    checkin_id = _make_patient(client)["check_in_ids"][0]
    token = client.get(f"/api/checkins/{checkin_id}/link").json()["token"]

    from api.main import app
    carer = TestClient(app)
    carer.post(f"/api/checkins/{checkin_id}/respond?token={token}", json={
        "taking_medication": True, "new_symptoms": False,
        "worse_than_last_week": False})

    again = carer.get(f"/api/checkins/by-token?token={token}")
    assert again.status_code == 409


def test_issuing_a_carer_link_is_clinician_only_and_scoped(client, db):
    _signup(client)
    checkin_id = _make_patient(client)["check_in_ids"][0]

    other = _second_org_client(TestClient, db)
    assert other.get(f"/api/checkins/{checkin_id}/link").status_code == 404


# ------------------------------------------------------------------- migration
def test_pre_auth_patients_are_adopted_at_signup(client, db):
    """An existing database has patients with no organisation. Every scoped query
    ignores them — safe, but it makes an upgraded install look empty. Adoption
    happens once, at bootstrap, when there is exactly one organisation to adopt
    into, and the count is reported rather than done quietly."""
    from api.database import Patient

    db.add(Patient(patient_ref="legacy-01", organisation_id=None))
    db.commit()

    body = _signup(client)
    assert body["adopted_existing_patients"] == 1
    refs = [p["patient_ref"] for p in client.get("/api/patients").json()]
    assert "legacy-01" in refs
