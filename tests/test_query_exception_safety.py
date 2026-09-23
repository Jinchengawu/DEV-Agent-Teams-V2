"""合成 sentinel：跨安全边界的异常属性/链不得保留底层对象。"""
import httpx
import pytest
from test_knowledge_query_integration import _active, _freeze
from test_knowledge_query_plan import FixtureMeasurement, compile_fixture

from agent_team_os.infrastructure.ollama import OllamaEmbeddingAdapter
from agent_team_os.modules.knowledge.index_ports import EmbeddingFailure
from agent_team_os.modules.knowledge.query_plan import QueryPlanError
from agent_team_os.shared.errors import ProductError

SENTINEL = 'SYNTHETIC-RAW-QUERY-NOT-A-SECRET'


def assert_safe(error):
    assert error.__cause__ is None
    assert error.__context__ is None
    assert SENTINEL not in repr(error.args)
    assert SENTINEL not in repr(vars(error))


@pytest.mark.parametrize('operation', ['get', 'post'])
@pytest.mark.parametrize('error_type', [httpx.ConnectError, httpx.ReadTimeout])
def test_transport_drops_raw_request_graph(operation, error_type):
    def fail(request):
        raise error_type(SENTINEL, request=request)
    adapter = OllamaEmbeddingAdapter(client=httpx.Client(
        base_url='http://localhost', transport=httpx.MockTransport(fail)))
    with pytest.raises(EmbeddingFailure) as caught:
        if operation == 'get':
            adapter.describe('model')
        else:
            adapter.embed((SENTINEL,), model_name='model', truncate=False)
    assert_safe(caught.value)


@pytest.mark.parametrize('method', ['measure', 'verify_qualification'])
def test_measurement_drops_raw_error_graph(method):
    measurement = FixtureMeasurement()
    def fail(*args):
        raise ValueError(SENTINEL)
    setattr(measurement, method, fail)
    with pytest.raises(QueryPlanError) as caught:
        compile_fixture(SENTINEL, measurement=measurement)
    assert_safe(caught.value)


def test_preflight_drops_compiler_error_graph(tmp_path, monkeypatch):
    actor, embedding, indexes, request = _active(tmp_path)
    def fail(*args):
        raise ValueError(SENTINEL)
    monkeypatch.setattr(indexes.query_measurement, 'measure', fail)
    with pytest.raises(ProductError) as caught:
        indexes.preflight_query(request)
    assert_safe(caught.value)
    assert embedding.calls == []


@pytest.mark.parametrize('category', ['transport', 'timeout', 'input_limit', 'provider_error'])
def test_executor_drops_provider_error_graph(tmp_path, monkeypatch, category):
    actor, embedding, indexes, request = _active(tmp_path)
    identity = _freeze(indexes, request)
    def fail(*args, **kwargs):
        try:
            raise ValueError(SENTINEL)
        except ValueError:
            raise EmbeddingFailure('KNOWLEDGE_OLLAMA_UNAVAILABLE', SENTINEL,
                                   category=category) from None
    monkeypatch.setattr(embedding, 'embed', fail)
    with pytest.raises(EmbeddingFailure) as caught:
        indexes.retrieve(actor, request, execution_identity=identity, admission=lambda: None)
    assert_safe(caught.value)


def test_invalid_unicode_does_not_retain_unicode_error():
    with pytest.raises(QueryPlanError) as caught:
        compile_fixture(SENTINEL + '\ud800')
    assert_safe(caught.value)
