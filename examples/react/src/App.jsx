import React, { useState } from "react";
import { createRoot } from "react-dom/client";

export function Counter() {
  const [selected, setSelected] = useState(false);
  return (
    <main>
      <h1>Praxile React Example</h1>
      <button
        aria-pressed={selected}
        className={selected ? "selected" : ""}
        onClick={() => setSelected(!selected)}
      >
        {selected ? "Selected" : "Select"}
      </button>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<Counter />);
