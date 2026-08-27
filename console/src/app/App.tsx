import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./shell/AppShell";
import { LoadingState } from "../shared/feedback/AsyncState";
import { AuthGate } from "../features/identity/AuthGate";

const DeliveriesPage = lazy(() => import("../features/deliveries/DeliveriesPage").then((module) => ({ default: module.DeliveriesPage })));
const DeliveryRunPage = lazy(() => import("../features/deliveries/DeliveryRunPage").then((module) => ({ default: module.DeliveryRunPage })));
const BoardPage = lazy(() => import("../features/board/BoardPage").then((module) => ({ default: module.BoardPage })));
const EvidencePage = lazy(() => import("../features/evidence/EvidencePage").then((module) => ({ default: module.EvidencePage })));
const SettingsPage = lazy(() => import("../features/settings/SettingsPage").then((module) => ({ default: module.SettingsPage })));
const OrchestrationPage = lazy(() => import("../features/orchestration/OrchestrationPage").then((module) => ({ default: module.OrchestrationPage })));
const AgentsPage = lazy(() => import("../features/agents/AgentsPage").then((module) => ({ default: module.AgentsPage })));
const KnowledgePage = lazy(() => import("../features/knowledge/KnowledgePage").then((module) => ({ default: module.KnowledgePage })));

export function App() {
  return <BrowserRouter><AuthGate><Suspense fallback={<LoadingState label="正在打开控制台模块…"/>}><Routes><Route element={<AppShell/>}>
    <Route index element={<Navigate to="/deliveries" replace/>}/>
    <Route path="deliveries" element={<DeliveriesPage/>}/>
    <Route path="deliveries/:deliveryId" element={<DeliveryRunPage/>}/>
    <Route path="board" element={<BoardPage/>}/>
    <Route path="orchestration" element={<OrchestrationPage/>}/>
    <Route path="agents" element={<AgentsPage/>}/>
    <Route path="knowledge" element={<KnowledgePage/>}/>
    <Route path="evidence" element={<EvidencePage/>}/>
    <Route path="settings" element={<SettingsPage/>}/>
    <Route path="*" element={<Navigate to="/deliveries" replace/>}/>
  </Route></Routes></Suspense></AuthGate></BrowserRouter>;
}
