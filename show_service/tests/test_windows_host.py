"""The sender host alias "windows": the Windows machine WSL runs on (the default route's gateway)."""
import pytest

from show_service import receiver

ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t00000000\t01F018AC\t0003\t0\t0\t0\t00000000\t0\t0\t0
eth0\t00F018AC\t00000000\t0001\t0\t0\t0\t00F0FFFF\t0\t0\t0
"""


def test_the_gateway_of_the_default_route(tmp_path):
    f = tmp_path / "route"
    f.write_text(ROUTE)
    assert receiver.windows_host(str(f)) == "172.24.240.1"


def test_no_default_route_is_an_oserror(tmp_path):
    f = tmp_path / "route"
    f.write_text(ROUTE.splitlines()[0] + "\n" + ROUTE.splitlines()[2] + "\n")
    with pytest.raises(OSError):
        receiver.windows_host(str(f))
