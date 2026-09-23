"""锁定 Stage 合同与原 payload 字段，不把内部 Query Plan 偷渡到 ACWM。"""
import ast
import inspect
import json
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

from test_delivery_knowledge_context_preparation import _preparation_input
from test_knowledge_context_runtime_guard import _stamp

from agent_team_os.knowledge_context_contract import (
    knowledge_context_artifact_contract,
    knowledge_context_artifact_contract_sha256,
)
from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage
from agent_team_os.modules.knowledge.context_application import (
    DeliveryKnowledgeContextPreparationService,
)
from agent_team_os.modules.orchestration import KnowledgeContextBinding
from agent_team_os.shared.hashes import sha256_bytes, sha256_json


def test_locked_acwm_contract_unchanged():
    contract = knowledge_context_artifact_contract()
    assert contract.id == 'knowledge-context-v1'
    assert contract.version == '1.0.0'
    assert contract.schema_uri is None
    assert knowledge_context_artifact_contract_sha256() == (
        '9e5a70ff5ca2c564b226b90ef30d3e9341edd478456fe347cc1a2b681d2da8a0'
    )


def test_stage_payload_shape_remains_v1_without_internal_plan_fields():
    tree = ast.parse(textwrap.dedent(inspect.getsource(
        DeliveryKnowledgeContextPreparationService._prepare_stage_context
    )))
    payloads = [node for node in ast.walk(tree) if isinstance(node, ast.Dict)
                and any(isinstance(v, ast.Constant) and v.value == 'knowledge-context-v1'
                        for v in node.values)]
    assert len(payloads) == 1
    keys = {key.value for key in payloads[0].keys if isinstance(key, ast.Constant)}
    assert keys == {
        'contract_id', 'contract_version', 'trust_class', 'instruction_authority',
        'delivery_id', 'project_id', 'stage_path', 'query', 'query_sha256',
        'project_description_snapshot', 'retrieval_policy_revision_id',
        'knowledge_binding_hash', 'approved_scope', 'authorization_stamp',
        'retrievals', 'citation_ids',
    }


def test_stage_runtime_serialization_retains_complete_original_query(tmp_path):
    service = object.__new__(DeliveryKnowledgeContextPreparationService)
    service.artifacts = ContentAddressedArtifactStorage(tmp_path / 'artifacts')
    service.repository = Mock()
    service.tenant = Mock()
    service.tenant.available_source_ids.return_value = ('source',)
    service.indexes = Mock()
    receipt = {'id': 'fixture-receipt', 'query_sha256': 'unused'}
    service.indexes.retrieve.return_value = SimpleNamespace(
        hits=(), receipt=Mock(model_dump=Mock(return_value=receipt)),
    )
    preparation = _preparation_input('fixture-delivery')
    stamp = _stamp('e' * 64)
    binding = KnowledgeContextBinding.model_validate(preparation.stage_bindings['requirements'])
    query = ('Project Name: fixture\nProject Description: 完整说明\n'
             'Delivery Goal: 实现受控知识上下文\nStage Path: requirements\n'
             'Stage Responsibility: 澄清需求边界')
    result = service._prepare_stage_context(
        run_id='fixture-run', preparation_input=preparation, stamp=stamp, actor=Mock(),
        approvals=(SimpleNamespace(binding_id='binding', id='approval'),), binding=binding,
        stage_path='requirements',
        project_description={'name': 'fixture', 'description': '完整说明'},
    )
    payload = json.loads(service.artifacts.get_bytes(result.artifact_reference))
    assert payload == {
        'contract_id': 'knowledge-context-v1', 'contract_version': '1.0.0',
        'trust_class': 'external-collaborative', 'instruction_authority': 'none',
        'delivery_id': preparation.delivery_id, 'project_id': preparation.project_id,
        'stage_path': 'requirements', 'query': query,
        'query_sha256': str(sha256_bytes(query.encode())),
        'project_description_snapshot': (
            preparation.project_description_snapshot.model_dump(mode='json')
        ),
        'retrieval_policy_revision_id': binding.retrieval_policy_revision_id,
        'knowledge_binding_hash': str(sha256_json(binding.model_dump(mode='json'))),
        'approved_scope': [], 'authorization_stamp': stamp.model_dump(mode='json'),
        'retrievals': [{'binding_id': 'binding', 'approval_id': 'approval',
                        'receipt': receipt, 'hits': []}],
        'citation_ids': [],
    }
