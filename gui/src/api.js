// .config/opencode/AGENTS.md APPLIED
// NutrientConflictsEvaluation Project AGENTS.md APPLIED
const BASE = "/api/v1";

async function request(path) {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return response.json();
}

export function evaluateFood(fdcId) {
  return request(`/foods/${fdcId}/evaluation`);
}

export function searchFoods(query) {
  return request(`/foods/search?q=${encodeURIComponent(query)}&limit=25`);
}

export function brandConflicts(owner, page = 1, size = 10) {
  return request(`/brands/${encodeURIComponent(owner)}/conflicts?page=${page}&size=${size}`);
}

export function brands(prefix = "") {
  return request(`/brands?limit=50&prefix=${encodeURIComponent(prefix)}`);
}

export function health() {
  return request("/health");
}
