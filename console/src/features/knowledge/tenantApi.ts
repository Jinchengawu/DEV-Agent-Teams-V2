import { useQuery } from "@tanstack/react-query";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";

export type TenantConnection = components["schemas"]["TenantConnection"];
export type TenantConnectionCreate = components["schemas"]["TenantConnectionCreate"];
export type TenantProviderBinding = components["schemas"]["TenantProviderBinding"];
export type TenantProviderBindingCreate = components["schemas"]["TenantProviderBindingCreate"];
export type ProviderSpace = components["schemas"]["ProviderSpace"];
export type ProviderNode = components["schemas"]["ProviderNode"];
export type KnowledgeSyncJob = components["schemas"]["KnowledgeSyncJob"];
export type TenantProviderSnapshot = components["schemas"]["TenantProviderSnapshotRecord"];
export type KnowledgeIndexCatalog = components["schemas"]["KnowledgeIndexCatalog"];
export type KnowledgeRetrievalResult = components["schemas"]["KnowledgeRetrievalResult"];
export type DeliveryKnowledgeContextOverview = components["schemas"]["DeliveryKnowledgeContextOverview"];
export type ProjectKnowledgeRetrievalOption = {
  provider_binding_id: string;
  index_revision_id: string;
  index_profile_revision_id: string;
  retrieval_policy_revision_id: string;
};

export const tenantKnowledgeKeys = {
  connections: ["knowledge", "tenant-connections"] as const,
  spaces: (connectionId: string) => ["knowledge", "tenant-connections", connectionId, "spaces"] as const,
  bindings: ["knowledge", "tenant-bindings"] as const,
  nodes: (bindingId: string) => ["knowledge", "tenant-bindings", bindingId, "nodes"] as const,
  catalog: ["knowledge", "index-catalog"] as const,
  syncJobs: (projectId: string, bindingId: string) => ["knowledge", projectId, bindingId, "sync-jobs"] as const,
  snapshots: (projectId: string, bindingId: string) => ["knowledge", projectId, bindingId, "snapshots"] as const,
  retrievalOptions: (projectId: string, bindingId: string) => ["knowledge", projectId, bindingId, "retrieval-options"] as const,
  deliveryContext: (projectId: string, deliveryId: string) => ["knowledge", projectId, deliveryId, "delivery-context"] as const,
};

export function useTenantConnections(enabled = true) {
  return useQuery({
    queryKey: tenantKnowledgeKeys.connections,
    queryFn: ({ signal }) => request<TenantConnection[]>("/v1/knowledge/connections", { signal }),
    enabled,
  });
}

export function useTenantBindings(enabled = true) {
  return useQuery({
    queryKey: tenantKnowledgeKeys.bindings,
    queryFn: ({ signal }) => request<TenantProviderBinding[]>("/v1/knowledge/provider-bindings-v2", { signal }),
    enabled,
  });
}

export function useConnectionSpaces(connectionId: string, enabled = true) {
  return useQuery({
    queryKey: tenantKnowledgeKeys.spaces(connectionId),
    queryFn: ({ signal }) => request<ProviderSpace[]>(`/v1/knowledge/connections/${encodeURIComponent(connectionId)}/spaces`, { signal }),
    enabled: enabled && Boolean(connectionId),
  });
}

export function useBindingNodes(bindingId: string) {
  return useQuery({
    queryKey: tenantKnowledgeKeys.nodes(bindingId),
    queryFn: ({ signal }) => request<ProviderNode[]>(`/v1/knowledge/provider-bindings-v2/${encodeURIComponent(bindingId)}/nodes`, { signal }),
    enabled: Boolean(bindingId),
  });
}

export function useProjectBindingNodes(projectId: string, bindingId: string, enabled = true) {
  return useQuery({
    queryKey: tenantKnowledgeKeys.nodes(`${projectId}:${bindingId}`),
    queryFn: ({ signal }) => request<ProviderNode[]>(`/v1/projects/${encodeURIComponent(projectId)}/knowledge-bindings/${encodeURIComponent(bindingId)}/nodes`, { signal }),
    enabled: enabled && Boolean(projectId && bindingId),
  });
}

export function useKnowledgeIndexCatalog(enabled = true) {
  return useQuery({
    queryKey: tenantKnowledgeKeys.catalog,
    queryFn: ({ signal }) => request<KnowledgeIndexCatalog>("/v1/knowledge/index-catalog", { signal }),
    enabled,
  });
}

export function useProjectKnowledgeSyncJobs(projectId: string, bindingId: string, enabled = true) {
  return useQuery({
    queryKey: tenantKnowledgeKeys.syncJobs(projectId, bindingId),
    queryFn: ({ signal }) => request<KnowledgeSyncJob[]>(`/v1/projects/${encodeURIComponent(projectId)}/knowledge-sync-jobs?binding_id=${encodeURIComponent(bindingId)}`, { signal }),
    enabled: enabled && Boolean(projectId && bindingId),
  });
}

export function useProjectKnowledgeSnapshots(projectId: string, bindingId: string, enabled = true) {
  return useQuery({
    queryKey: tenantKnowledgeKeys.snapshots(projectId, bindingId),
    queryFn: ({ signal }) => request<TenantProviderSnapshot[]>(`/v1/projects/${encodeURIComponent(projectId)}/knowledge-snapshots?binding_id=${encodeURIComponent(bindingId)}`, { signal }),
    enabled: enabled && Boolean(projectId && bindingId),
  });
}

export function useProjectKnowledgeRetrievalOptions(projectId: string, bindingId: string, enabled: boolean) {
  return useQuery({
    queryKey: tenantKnowledgeKeys.retrievalOptions(projectId, bindingId),
    queryFn: ({ signal }) => request<ProjectKnowledgeRetrievalOption[]>(`/v1/projects/${encodeURIComponent(projectId)}/knowledge-retrieval-options?provider_binding_id=${encodeURIComponent(bindingId)}`, { signal }),
    enabled: enabled && Boolean(projectId && bindingId),
  });
}

export function useDeliveryKnowledgeContext(projectId: string, deliveryId: string) {
  return useQuery({
    queryKey: tenantKnowledgeKeys.deliveryContext(projectId, deliveryId),
    queryFn: ({ signal }) => request<DeliveryKnowledgeContextOverview>(`/v1/deliveries/${encodeURIComponent(deliveryId)}/knowledge-context`, { signal }),
    enabled: Boolean(projectId && deliveryId),
    refetchInterval: (query) => ["completed", "failed", "cancelled", "rejected"].includes(query.state.data?.delivery_status ?? "") ? false : 1000,
  });
}
