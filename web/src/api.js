const API_BASE = import.meta.env.VITE_PRAXILE_API_BASE || "";

export async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  });
  const data = await response.json();
  if (!data.ok) {
    throw new Error(data.error || "Praxile request failed");
  }
  return data.result;
}
