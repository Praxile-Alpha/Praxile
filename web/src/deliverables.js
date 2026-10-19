export function parseContract(text) {
  if (new TextEncoder().encode(text).length > 65536) throw new Error("Requirements exceed 64 KiB.");
  let value;
  try { value = JSON.parse(text); } catch { throw new Error("Requirements must be valid JSON."); }
  if (!value || Array.isArray(value) || !["praxile.deliverable_contract.v1", "praxile.deliverable_contract.v2"].includes(value.schema_version)
      || value.source !== "user_request" || !Array.isArray(value.outputs)) {
    throw new Error("A V1/V2 user_request contract with outputs is required.");
  }
  if (value.schema_version.endsWith(".v2") && (!value.state_equals || Array.isArray(value.state_equals)
      || typeof value.state_equals !== "object" || !Object.keys(value.state_equals).length)) {
    throw new Error("V2 requires state_equals constraints.");
  }
  return value;
}

export const initialContract = JSON.stringify({
  schema_version: "praxile.deliverable_contract.v1", source: "user_request",
  outputs: [{ path: "report.json", media_type: "application/json", required: true }]
}, null, 2);
