from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from ...shared.clock import Clock, SystemClock
from ...shared.errors import ProductError
from ...shared.ids import new_id
from ...shared.permissions import Permission, Role, permits
from .domain import KnowledgeActor
from .provider_domain import (
    ProviderBinding,
    ProviderBindingCreate,
    ProviderSnapshotRecord,
    ProviderSyncResult,
    ProviderSyncRun,
    ProviderSyncStatus,
)
from .provider_ports import (
    KnowledgeProviderResolver,
    ProviderActorResolver,
    ProviderFailure,
    ProviderKnowledgeRepository,
)


class ProviderKnowledgeManager:
    def __init__(
        self,
        repository: ProviderKnowledgeRepository,
        resolver: KnowledgeProviderResolver,
        actor_resolver: ProviderActorResolver,
        clock: Clock | None = None,
    ) -> None:
        self.repository = repository
        self.resolver = resolver
        self.actor_resolver = actor_resolver
        self.clock = clock or SystemClock()

    def create_binding(
        self, actor: KnowledgeActor, request: ProviderBindingCreate
    ) -> ProviderBinding:
        if actor.role != Role.ADMINISTRATOR:
            raise _permission_denied()
        now = self.clock.now()
        binding = ProviderBinding(
            id=new_id(),
            provider_kind=request.provider_kind,
            display_name=request.display_name,
            external_space_id=request.external_space_id,
            credential_ref=request.credential_ref,
            enabled=True,
            version=1,
            created_by=actor.user_id,
            created_at=now,
            updated_at=now,
        )
        created = self.repository.create_binding(binding)
        if created is None:
            raise ProductError(
                code="KNOWLEDGE_PROVIDER_BINDING_CONFLICT",
                title="知识来源绑定冲突",
                detail="该外部知识空间已经存在绑定。",
                repair="刷新绑定列表后复用现有绑定。",
            )
        return created

    def list_bindings(self, actor: KnowledgeActor) -> tuple[ProviderBinding, ...]:
        if actor.role != Role.ADMINISTRATOR:
            raise _permission_denied()
        return self.repository.list_bindings()

    def sync(
        self,
        actor: KnowledgeActor,
        binding_id: str,
        source_id: str,
    ) -> ProviderSyncResult:
        if not permits(actor.role, Permission.WIKI_EDIT):
            raise _permission_denied()
        binding = self.repository.get_binding(binding_id)
        if binding is None:
            raise ProductError(
                code="KNOWLEDGE_PROVIDER_BINDING_NOT_FOUND",
                title="知识来源绑定不存在",
                detail="指定的外部知识绑定已不存在。",
                repair="刷新绑定列表后重试。",
                status_code=404,
            )
        if not binding.enabled:
            raise ProductError(
                code="KNOWLEDGE_PROVIDER_BINDING_DISABLED",
                title="知识来源绑定已禁用",
                detail="禁用的绑定不能发起新同步。",
                repair="由管理员启用该绑定后重试。",
            )
        provider_actor = self.actor_resolver.resolve(binding, actor)
        if provider_actor.product_user_id != actor.user_id:
            raise ProductError(
                code="KNOWLEDGE_PROVIDER_ACTOR_MISMATCH",
                title="外部身份不匹配",
                detail="外部知识身份与当前产品账户不一致。",
                repair="重新为当前账户完成飞书授权。",
                status_code=403,
            )
        started = self.clock.now()
        running = ProviderSyncRun(
            id=new_id(),
            binding_id=binding.id,
            source_id=source_id,
            status=ProviderSyncStatus.RUNNING,
            started_at=started,
        )
        self.repository.begin_sync(running)
        try:
            provider = self.resolver.resolve(binding)
            fetched = provider.fetch_snapshot(provider_actor, source_id)
            snapshot = ProviderSnapshotRecord(
                **fetched.model_dump(),
                id=_snapshot_id(binding.id, source_id, fetched.provider_revision),
                binding_id=binding.id,
                fetched_by_product_user_id=actor.user_id,
                fetched_by_provider_user_id=provider_actor.provider_user_id,
            )
            completed = running.model_copy(
                update={
                    "status": ProviderSyncStatus.SUCCEEDED,
                    "provider_revision": snapshot.provider_revision,
                    "snapshot_id": snapshot.id,
                    "snapshot_sha256": snapshot.content_sha256,
                    "completed_at": self.clock.now(),
                }
            )
            persisted = self.repository.complete_sync(completed, snapshot)
            if persisted is None:
                failed = _failed_run(
                    running,
                    "KNOWLEDGE_PROVIDER_REVISION_CONFLICT",
                    self.clock.now(),
                )
                self.repository.fail_sync(failed)
                raise ProductError(
                    code="KNOWLEDGE_PROVIDER_REVISION_CONFLICT",
                    title="Provider Revision 内容冲突",
                    detail="同一 Provider Revision 返回了不同的标准化内容。",
                    repair="停止使用该来源并检查外部 Provider 审计记录。",
                )
            return ProviderSyncResult(run=completed, snapshot=persisted)
        except ProviderFailure as error:
            status = (
                ProviderSyncStatus.UNAVAILABLE
                if error.unavailable
                else ProviderSyncStatus.FAILED
            )
            failed = _failed_run(running, error.code, self.clock.now(), status)
            self.repository.fail_sync(failed)
            return ProviderSyncResult(run=failed)
        except ProductError:
            raise
        except Exception:
            failed = _failed_run(
                running,
                "KNOWLEDGE_PROVIDER_UNEXPECTED_FAILURE",
                self.clock.now(),
            )
            self.repository.fail_sync(failed)
            return ProviderSyncResult(run=failed)


def _snapshot_id(binding_id: str, source_id: str, revision: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"agent-team-os:provider-snapshot:{binding_id}:{source_id}:{revision}",
        )
    )


def _failed_run(
    running: ProviderSyncRun,
    error_code: str,
    completed_at: datetime,
    status: ProviderSyncStatus = ProviderSyncStatus.FAILED,
) -> ProviderSyncRun:
    return running.model_copy(
        update={
            "status": status,
            "error_code": error_code,
            "completed_at": completed_at.astimezone(UTC),
        }
    )


def _permission_denied() -> ProductError:
    return ProductError(
        code="KNOWLEDGE_PROVIDER_PERMISSION_DENIED",
        title="外部知识权限不足",
        detail="当前账户无权配置或同步外部知识。",
        repair="联系管理员调整账户角色。",
        status_code=403,
    )
