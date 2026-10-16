"""Reviewer lists expose a display name without replacing the stable uploader ID."""
import pytest

import main
from layers import auth, organizations
from layers.account_lifecycle import DELETED_USER


@pytest.mark.parametrize("endpoint,verified", [("/pending", False), ("/documents/verified", True)])
@pytest.mark.parametrize("case,expected", [
    ("username", "演示员工"),
    ("email", "12****89@example.com"),
    ("empty", "-"),
    ("deleted", DELETED_USER),
    ("missing", "-"),
])
def test_reviewer_document_uploader_display_rules(
    client, auth_headers, monkeypatch, endpoint, verified, case, expected
):
    headers, reviewer = auth_headers("reviewer")
    org = organizations.create_organization("展示组织", None)
    uploader = auth.register_user("演示员工", "SyntheticPassword123!", "employee")
    uploader_id = uploader["user_id"]
    with auth._connect() as conn:
        conn.execute("INSERT INTO user_organizations(user_id,organization_id) VALUES (?,?)",
                     (reviewer["user_id"], org["id"]))
        conn.execute("UPDATE users SET email=? WHERE user_id=?",
                     ("123456789@example.com", uploader_id))
        if case in {"email", "empty"}:
            conn.execute("UPDATE users SET username='', email=? WHERE user_id=?",
                         ("123456789@example.com" if case == "email" else "", uploader_id))
    if case == "deleted":
        uploader_id = DELETED_USER
    elif case == "missing":
        uploader_id = "missing-synthetic-user"
    auth.register_document("display-doc", "说明.md", uploader_id, organization_id=org["id"])
    if verified:
        auth.approve_document("display-doc", reviewer["user_id"])
    # Display-name projection is independent of Chroma chunk-count enrichment.
    monkeypatch.setattr(main, "_merge_document_chunks", lambda documents, **kwargs: documents)
    response = client.get(endpoint, params={"organization_id": org["id"]}, headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    document = response.json()["documents"][0]
    assert document["uploaded_by"] == uploader_id
    assert document["uploaded_by_display_name"] == expected
    assert "uploader_email" not in document
    assert "uploader_username" not in document
    assert "123456789@example.com" not in response.text


@pytest.mark.parametrize("endpoint", ["/pending", "/documents/verified"])
def test_uploader_display_does_not_expand_reviewer_scope(client, auth_headers, monkeypatch, endpoint):
    headers, _ = auth_headers("reviewer")
    org = organizations.create_organization("其他组织", None)
    auth.register_document("other-doc", "其他资料.md", "unknown", organization_id=org["id"])
    if endpoint.endswith("verified"):
        auth.approve_document("other-doc", "synthetic-reviewer")
    monkeypatch.setattr(main, "_merge_document_chunks", lambda documents, **kwargs: documents)
    response = client.get(endpoint, headers=headers)
    assert response.status_code == 200
    assert response.json()["documents"] == []
