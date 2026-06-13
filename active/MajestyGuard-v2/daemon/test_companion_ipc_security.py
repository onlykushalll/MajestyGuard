import inspect

import companion_ipc


def _reset_key_cache():
    companion_ipc._MUTUAL_AUTH_KEY = None


def test_whcdf_hmac_key_fails_closed_without_secure_config(monkeypatch):
    _reset_key_cache()
    monkeypatch.delenv("MAJESTYGUARD_MUTUAL_AUTH_KEY", raising=False)
    monkeypatch.delenv("MG_ALLOW_INSECURE_WHCDF_ENV_KEY", raising=False)

    assert companion_ipc._get_mutual_auth_key() is None


def test_whcdf_env_hmac_key_is_ignored_unless_explicitly_allowed(monkeypatch):
    _reset_key_cache()
    monkeypatch.setenv("MAJESTYGUARD_MUTUAL_AUTH_KEY", "aa" * 32)
    monkeypatch.delenv("MG_ALLOW_INSECURE_WHCDF_ENV_KEY", raising=False)

    assert companion_ipc._get_mutual_auth_key() is None


def test_whcdf_env_hmac_key_requires_dev_opt_in(monkeypatch):
    _reset_key_cache()
    monkeypatch.setenv("MAJESTYGUARD_MUTUAL_AUTH_KEY", "aa" * 32)
    monkeypatch.setenv("MG_ALLOW_INSECURE_WHCDF_ENV_KEY", "1")

    assert companion_ipc._get_mutual_auth_key() == bytes.fromhex("aa" * 32)


def test_whcdf_local_pipe_clients_are_denied_by_default(monkeypatch):
    monkeypatch.delenv("MG_WHCDF_ALLOW_LOCAL_PIPE_CLIENTS", raising=False)

    assert not companion_ipc._local_pipe_clients_allowed()


def test_whcdf_pipe_does_not_install_null_dacl():
    source = inspect.getsource(companion_ipc._build_security_attributes)

    assert "SetSecurityDescriptorDacl(1, None" not in source


def test_whcdf_rejects_unauthenticated_pipe_client_before_face_state(monkeypatch):
    monkeypatch.setattr(companion_ipc, "_local_pipe_clients_allowed", lambda: False)

    def forbidden_face_probe():
        raise AssertionError("FaceState must not be read before client auth")

    monkeypatch.setattr(companion_ipc.FaceState, "is_authorized", staticmethod(forbidden_face_probe))

    key, reason = companion_ipc._authorize_hmac_request()

    assert key is None
    assert reason == "client-auth-not-configured"


def test_whcdf_rejects_missing_hmac_key_before_face_state(monkeypatch):
    _reset_key_cache()
    monkeypatch.setattr(companion_ipc, "_local_pipe_clients_allowed", lambda: True)
    monkeypatch.delenv("MAJESTYGUARD_MUTUAL_AUTH_KEY", raising=False)
    monkeypatch.delenv("MG_ALLOW_INSECURE_WHCDF_ENV_KEY", raising=False)

    def forbidden_face_probe():
        raise AssertionError("FaceState must not be read before key configuration")

    monkeypatch.setattr(companion_ipc.FaceState, "is_authorized", staticmethod(forbidden_face_probe))

    key, reason = companion_ipc._authorize_hmac_request()

    assert key is None
    assert reason == "mutual-auth-key-not-configured"


def test_whcdf_authorizes_only_after_client_key_and_face_gates(monkeypatch):
    _reset_key_cache()
    companion_ipc.FaceState.clear()
    companion_ipc.FaceState.set_recognized(liveness_score=0.92)
    monkeypatch.setattr(companion_ipc, "_local_pipe_clients_allowed", lambda: True)
    monkeypatch.setenv("MAJESTYGUARD_MUTUAL_AUTH_KEY", "aa" * 32)
    monkeypatch.setenv("MG_ALLOW_INSECURE_WHCDF_ENV_KEY", "1")

    key, reason = companion_ipc._authorize_hmac_request()

    assert key == bytes.fromhex("aa" * 32)
    assert reason is None
    companion_ipc.FaceState.clear()
