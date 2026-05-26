"""
Pin the operator-reboot endpoint contract.

Critical security: the endpoint must never accept user-supplied
command strings. Only the keys `backend`, `worker`, `nginx` are
accepted; the actual `argv` lists are hardcoded constants.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from routes.emergent_admin.ops import (
    ALLOWED_RESTART_COMMANDS,
    RebootBody,
    router,
)


def test_endpoint_registered():
    paths = {r.path for r in router.routes if hasattr(r, "path")}
    assert "/reboot" in paths
    assert "/reboot/_meta" in paths


def test_allowlist_keys_are_exactly_three():
    """Lock the set of restartable services. Adding a new one must
    be intentional + reviewed."""
    assert set(ALLOWED_RESTART_COMMANDS.keys()) == {
        "backend", "worker", "nginx"}


def test_argv_lists_are_hardcoded_static_lists():
    """Every argv MUST be a List[str] with no `;`, `|`, `&&`, `>`,
    `$()`, or backticks — i.e. no shell injection surface."""
    forbidden_substrings = (";", "|", "&&", ">", "<", "$(", "`", "\n")
    for key, cfg in ALLOWED_RESTART_COMMANDS.items():
        argv = cfg["argv"]
        assert isinstance(argv, list), (
            f"{key} argv must be a list, not {type(argv)}")
        for part in argv:
            assert isinstance(part, str)
            for bad in forbidden_substrings:
                assert bad not in part, (
                    f"{key} argv contains forbidden char {bad!r}: {part}")


def test_backend_command_targets_correct_service():
    argv = ALLOWED_RESTART_COMMANDS["backend"]["argv"]
    assert "vision-backend.service" in argv
    assert "restart" in argv
    assert any("systemctl" in a for a in argv)


def test_worker_command_targets_supervisorctl():
    argv = ALLOWED_RESTART_COMMANDS["worker"]["argv"]
    assert "research_worker" in argv
    assert any("supervisorctl" in a for a in argv)


def test_nginx_uses_reload_not_restart():
    """Nginx MUST be reloaded (no dropped connections), not restarted."""
    argv = ALLOWED_RESTART_COMMANDS["nginx"]["argv"]
    assert "reload" in argv
    assert "restart" not in argv


def test_every_command_uses_sudo_n_for_passwordless():
    """`sudo -n` fails fast if passwordless sudo isn't configured —
    we never want the endpoint to hang waiting for a TTY password
    prompt. Each argv must include `-n`."""
    for key, cfg in ALLOWED_RESTART_COMMANDS.items():
        assert "-n" in cfg["argv"], (
            f"{key} must use `sudo -n` to fail fast without a TTY")


def test_reboot_body_default_includes_all_three():
    body = RebootBody()
    assert set(body.services) == {"backend", "worker", "nginx"}


def test_reboot_body_can_select_a_subset():
    body = RebootBody(services=["worker"])
    assert body.services == ["worker"]


def test_every_argv_has_a_timeout_bound():
    """Defensive — every command must declare a max runtime so a
    hung systemctl can't lock the API request forever."""
    for key, cfg in ALLOWED_RESTART_COMMANDS.items():
        assert isinstance(cfg.get("timeout_s"), int)
        assert 5 <= cfg["timeout_s"] <= 60
