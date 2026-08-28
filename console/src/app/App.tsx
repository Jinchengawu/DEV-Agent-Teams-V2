import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./shell/AppShell";
import { LoadingState } from "../shared/feedback/AsyncState";
import { PageErrorBoundary } from "../shared/feedback/PageErrorBoundary";
import { AuthGate } from "../features/identity/AuthGate";

const DeliveriesPage = lazy(() => import("../features/deliveries/DeliveriesPage").then((module) => ({ default: module.DeliveriesPage })));
const DeliveryRunPage = lazy(() => import("../features/deliveries/DeliveryRunPage").then((module) => ({ default: module.DeliveryRunPage })));
const BoardPage = lazy(() => import("../features/board/BoardPage").then((module) => ({ default: module.BoardPage })));
const EvidencePage = lazy(() => import("../features/evidence/EvidencePage").then((module) => ({ default: module.EvidencePage })));
const SettingsPage = lazy(() => import("../features/settings/SettingsPage").then((module) => ({ default: module.SettingsPage })));
const OrchestrationPage = lazy(() => import("../features/orchestration/OrchestrationPage").then((module) => ({ default: module.OrchestrationPage })));
const AgentsPage = lazy(() => import("../features/agents/AgentsPage").then((module) => ({ default: module.AgentsPage })));
const KnowledgePage = lazy(() => import("../features/knowledge/KnowledgePage").then((module) => ({ default: module.KnowledgePage })));
const ProjectsPage = lazy(() => import("../features/projects/ProjectsPage").then((module) => ({ default: module.ProjectsPage })));
const ProjectOverviewPage = lazy(() => import("../features/projects/ProjectOverviewPage").then((module) => ({ default: module.ProjectOverviewPage })));

export function App() {
  return <BrowserRouter><AuthGate><PageErrorBoundary><Suspense fallback={<LoadingState label="正在打开控制台模块…"/>}><Routes><Route element={<AppShell/>}>
    <Route index element={<Navigate to="/projects" replace/>}/>
    <Route path="projects" element={<ProjectsPage/>}/>
    <Route path="projects/:projectId/overview" element={<ProjectOverviewPage/>}/>
    <Route path="projects/:projectId/deliveries" element={<DeliveriesPage/>}/>
    <Route path="projects/:projectId/deliveries/:deliveryId" element={<DeliveryRunPage/>}/>
    <Route path="projects/:projectId/board" element={<BoardPage/>}/>
    <Route path="projects/:projectId/knowledge" element={<KnowledgePage/>}/>
    <Route path="projects/:projectId/evidence" element={<EvidencePage/>}/>
    <Route path="deliveries" element={<Navigate to="/projects/legacy-default/deliveries" replace/>}/>
    <Route path="deliveries/:deliveryId" element={<DeliveryRunPage/>}/>
    <Route path="board" element={<Navigate to="/projects/legacy-default/board" replace/>}/>
    <Route path="orchestration" element={<OrchestrationPage/>}/>
    <Route path="agents" element={<AgentsPage/>}/>
    <Route path="knowledge" element={<Navigate to="/projects/legacy-default/knowledge" replace/>}/>
    <Route path="evidence" element={<Navigate to="/projects/legacy-default/evidence" replace/>}/>
    <Route path="settings" element={<SettingsPage/>}/>
    <Route path="*" element={<Navigate to="/projects" replace/>}/>
  </Route></Routes></Suspense></PageErrorBoundary></AuthGate></BrowserRouter>;
}
