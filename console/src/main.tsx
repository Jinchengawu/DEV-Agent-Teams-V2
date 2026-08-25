import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@xyflow/react/dist/style.css";
import "./styles.css";
import "./orchestration.css";
import "./app/console-theme.css";
import { App } from "./app/App";
import { DesignSystemProvider } from "./app/design-system/DesignSystemProvider";

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 500 } } });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><DesignSystemProvider><QueryClientProvider client={client}><App /></QueryClientProvider></DesignSystemProvider></React.StrictMode>,
);
