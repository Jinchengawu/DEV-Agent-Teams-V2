import httpx
import pytest

from agent_team_os.infrastructure.ollama import OllamaEmbeddingAdapter
from agent_team_os.modules.knowledge.index_ports import EmbeddingFailure


@pytest.mark.parametrize(
    ('status', 'message', 'category'),
    [
        (400, 'input length exceeds maximum context length SECRET', 'input_limit'),
        (400, 'invalid request SECRET', 'invalid_request'),
        (429, 'SECRET', 'rate_limited'),
        (503, 'SECRET', 'provider_error'),
    ],
)
def test_safe_embedding_error_never_retains_provider_body(status, message, category):
    adapter = OllamaEmbeddingAdapter(client=httpx.Client(
        base_url='http://localhost',
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json={'error': message})),
    ))
    with pytest.raises(EmbeddingFailure) as caught:
        adapter.embed(('PRIVATE QUERY',), model_name='test-model', truncate=False)
    error = caught.value
    assert error.http_status == status
    assert error.category == category
    assert 'SECRET' not in repr(vars(error))
    assert 'PRIVATE QUERY' not in repr(vars(error))
    assert error.__cause__ is None


def test_invalid_json_error_does_not_retain_response_cause():
    adapter = OllamaEmbeddingAdapter(client=httpx.Client(
        base_url='http://localhost',
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'SECRET RAW BODY')),
    ))
    with pytest.raises(EmbeddingFailure) as caught:
        adapter.embed(('PRIVATE QUERY',), model_name='test-model', truncate=False)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.category == 'invalid_response'
