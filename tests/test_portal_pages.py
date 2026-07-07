from fastapi.testclient import TestClient

from imv.portal.app import create_app


def test_member_pages_derive_homepage_shell_without_new_stylesheet(tmp_path):
    app = create_app(tmp_path / "data", tmp_path / "releases", testing=True)
    with TestClient(app) as client:
        for path in ("/member/register", "/member/login", "/member/releases", "/privacy"):
            response = client.get(path)
            assert response.status_code == 200
            assert "INDEX AI" in response.text
            assert "/member/assets/styles.css" not in response.text
    app.state.store.close()
