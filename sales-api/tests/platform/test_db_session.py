from app.db.session import CONNECT_ARGS


def test_sales_connections_run_without_jit():
    """JIT compiling the Status CASE took seconds per list (2026-11-12): Sales' connections turn it off."""
    assert CONNECT_ARGS["server_settings"]["jit"] == "off"
