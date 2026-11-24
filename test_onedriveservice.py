import os
from unittest.mock import patch, MagicMock
import pytest

os.environ["CLIENT_SECRET"] = "dummy_secret_for_testing"
import onedrive_service

@pytest.fixture
def temp_token_file(tmp_path, monkeypatch):
    fake_file = tmp_path / "test_tokens.json"
    monkeypatch.setattr(onedrive_service, "TOKEN_FILE", str(fake_file))
    return fake_file

def test_get_teacher_login_url():
    url = onedrive_service.get_teacher_login_url("prof_sharma")
    assert "https://login.microsoftonline.com" in url

    assert "state=" in url

def test_save_teacher_token_success(temp_token_file):
    login_url = onedrive_service.get_teacher_login_url("prof_sharma")
    state = login_url.split("state=")[1].split("&")[0]

    fake_res = {"refresh_token": "fake_refresh_token_123"}
    with patch.object(onedrive_service.msal_app, "acquire_token_by_authorization_code", return_value=fake_res):
        result = onedrive_service.save_teacher_token(state, "fake_auth_code")

    assert result == {"success": True, "teacher_id": "prof_sharma"}
    assert onedrive_service._load_tokens() == {"prof_sharma": "fake_refresh_token_123"}

def test_save_teacher_token_invalid_state(temp_token_file):
    result = onedrive_service.save_teacher_token("wrong_state", "fake_code")
    assert result["success"] is False

def test_upload_routes_to_correct_folders(temp_token_file, tmp_path):
    onedrive_service._save_tokens({"prof_sharma": "fake_refresh_token"})
    dummy_file = tmp_path / "report.pdf"
    dummy_file.write_bytes(b"dummy pdf content")

    fake_token = {"access_token": "fake_access_token"}
    fake_put = MagicMock(status_code=201)
    fake_put.json.return_value = {"webUrl": "https://live.com"}

    with patch.object(onedrive_service.msal_app, "acquire_token_by_refresh_token", return_value=fake_token), \
         patch("requests.put", return_value=fake_put) as mock_put:

        res_asg = onedrive_service.upload_to_teacher_onedrive("prof_sharma", str(dummy_file), "assignment", "Rahul")
        assert res_asg["success"] is True
        assert "Faculty_Portal/Assignments/Rahul_report.pdf" in mock_put.call_args[0][0]

        res_od = onedrive_service.upload_to_teacher_onedrive("prof_sharma", str(dummy_file), "od", "Priya")
        assert res_od["success"] is True
        assert "Faculty_Portal/OD_Requests/Priya_report.pdf" in mock_put.call_args[0][0]
