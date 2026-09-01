import { useState } from "react";
import { ChatPage } from "./pages/ChatPage";
import { SearchPage } from "./pages/SearchPage";
import { DocumentsPage } from "./pages/DocumentsPage";

type Tab = "chat" | "search" | "documents";

export function App() {
  const [tab, setTab] = useState<Tab>("chat");

  return (
    <div style={{ maxWidth: 960, margin: "0 auto", padding: 24 }}>
      <h1>OpenRAG Lab</h1>
      <nav style={{ display: "flex", gap: 12, marginBottom: 24 }}>
        {(["chat", "search", "documents"] as Tab[]).map((item) => (
          <button
            key={item}
            onClick={() => setTab(item)}
            style={{
              padding: "8px 16px",
              fontWeight: tab === item ? 700 : 400,
            }}
          >
            {item}
          </button>
        ))}
      </nav>
      {tab === "chat" && <ChatPage />}
      {tab === "search" && <SearchPage />}
      {tab === "documents" && <DocumentsPage />}
    </div>
  );
}
