import { Navigate } from "react-router-dom";
import { LEGACY_PROJECT_ID, projectPath, readActiveProjectId, useProject, useProjects } from "../../entities/project/api";
import { LoadingState } from "../../shared/feedback/AsyncState";
import { useSetupReadiness } from "../setup/api";

export function LandingRoute() {
  const projects = useProjects();
  const remembered = readActiveProjectId();
  const selected = projects.data?.find((project) => project.id === remembered && project.id !== LEGACY_PROJECT_ID)
    ?? projects.data?.find((project) => project.id !== LEGACY_PROJECT_ID);
  const detail = useProject(selected?.id ?? "", Boolean(selected));
  const readiness = useSetupReadiness(selected?.id, Boolean(selected));
  if (projects.isPending || (selected && (detail.isPending || readiness.isPending))) return <LoadingState label="正在选择安全落点…"/>;
  if (!selected || detail.error || readiness.error || detail.data?.onboarding.status !== "ready" || readiness.data?.status !== "ready") return <Navigate to="/setup" replace/>;
  return <Navigate to={projectPath(selected.id, "deliveries")} replace/>;
}
