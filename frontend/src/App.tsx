import { useState } from "react";
import { ChatPage } from "./pages/ChatPage";
import { SearchPage } from "./pages/SearchPage";
import { DocumentsPage } from "./pages/DocumentsPage";

type Tab = "chat" | "search" | "documents";

const NAV_ITEMS: { key: Tab; label: string }[] = [
  { key: "chat", label: "Chat" },
  { key: "search", label: "Search" },
  { key: "documents", label: "Documents" },
];

export function App() {
  const [tab, setTab] = useState<Tab>("chat");

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">OpenRAG Lab</div>
        {NAV_ITEMS.map((item) => (
          <button
            key={item.key}
            className={`nav-item ${tab === item.key ? "active" : ""}`}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </aside>
      <main className="main">
        <div className="page">
          {tab === "chat" && <ChatPage />}
          {tab === "search" && <SearchPage />}
          {tab === "documents" && <DocumentsPage />}
        </div>
      </main>
    </div>
  );
}
