import { parseHealthResponse } from "./health";

const HEALTH_CONTRACT_HEADER = "X-Health-Contract";
const HEALTH_CONTRACT_HEADER_VALUE = "health-contract-v1";

const statusElement = document.querySelector<HTMLElement>('[data-testid="health-status"]');
const errorElement = document.querySelector<HTMLElement>('[data-testid="health-error"]');

async function showHealth(): Promise<void> {
  if (!statusElement || !errorElement) throw new Error("HEALTH_PAGE_ELEMENTS_MISSING");
  const requestedStatus = new URLSearchParams(window.location.search).get("status") ?? "ok";
  try {
    const response = await fetch(`/api/health?status=${encodeURIComponent(requestedStatus)}`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error("HEALTH_REQUEST_FAILED");
    if (response.headers.get(HEALTH_CONTRACT_HEADER) !== HEALTH_CONTRACT_HEADER_VALUE) {
      throw new Error("HEALTH_CONTRACT_HEADER_INVALID");
    }
    const result = parseHealthResponse(await response.json());
    statusElement.textContent = result.status;
    errorElement.hidden = true;
  } catch {
    statusElement.textContent = "unavailable";
    errorElement.textContent = "暂时无法读取健康状态，请稍后重试。";
    errorElement.hidden = false;
  }
}

void showHealth();
