"""Extra incompatible-mode regressions; offline doubles only."""
from types import SimpleNamespace as NS
import pytest
from test_no_dut_boundary import cf, Config, body

@pytest.mark.parametrize('kind', ['mock', 'hardware-lock'])
def test_rejects_environment_modes(cf, monkeypatch, kind):
    if kind == 'mock':
        monkeypatch.setattr(cf, 'IS_MOCK_MODE', True)
        monkeypatch.setattr('lib.mock_router.get_mock_server', lambda: NS(url='unused'))
    else:
        monkeypatch.setenv('TOLLGATE_USE_HARDWARE_LOCK', '1')
    with pytest.raises(pytest.UsageError, match='no-dut'):
        cf.pytest_configure(Config())


def test_router_rejects_rust_v2_shortcut(cf):
    with pytest.raises(pytest.UsageError, match='no-dut'):
        body(cf.router)(NS(config=Config()), NS(is_rust_v2=True))


def test_option_registered(cf):
    class Parser:
        options = {}
        def addoption(self, name, **kw):
            self.options[name] = kw
    parser = Parser()
    cf.pytest_addoption(parser)
    assert parser.options['--no-dut']['action'] == 'store_true'
