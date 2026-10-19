export function parseContract(text) {
  if (new TextEncoder().encode(text).length > 65536) throw new Error("Requirements exceed 64 KiB.");
  let value;
  try { value = JSON.parse(text); } catch { throw new Error("Requirements must be valid JSON."); }
  if (!value || Array.isArray(value) || !["praxile.deliverable_contract.v1", "praxile.deliverable_contract.v2", "praxile.deliverable_contract.v3"].includes(value.schema_version)
      || value.source !== "user_request" || !Array.isArray(value.outputs)) {
    throw new Error("A V1/V2/V3 user_request contract with outputs is required.");
  }
  if (value.schema_version.endsWith(".v3")) {
    const preserved = value.unchanged_files;
    if (!value.state_equals || Array.isArray(value.state_equals) || typeof value.state_equals !== "object"
        || !preserved || Array.isArray(preserved) || typeof preserved !== "object"
        || Object.keys(preserved).length < 1 || Object.keys(preserved).length > 100
        || Object.values(preserved).some((hash) => typeof hash !== "string" || !/^[0-9a-f]{64}$/.test(hash))) {
      throw new Error("V3 requires state_equals and 1 to 100 unchanged_files with lowercase SHA-256 values.");
    }
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
