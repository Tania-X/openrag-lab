import { useState } from "react";
import { search, SearchResult } from "../lib/api";

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [rerank, setRerank] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError("");
    try {
      const data = await search(query, rerank);
      setResults(data.results);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <h2>Search</h2>
      <form onSubmit={handleSubmit} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="输入检索词..."
          style={{ flex: 1, padding: 8 }}
        />
        <label>
          <input type="checkbox" checked={rerank} onChange={(e) => setRerank(e.target.checked)} />
          Rerank
        </label>
        <button type="submit" disabled={loading}>
          {loading ? "检索中..." : "检索"}
        </button>
      </form>
      {error && <p style={{ color: "red" }}>{error}</p>}
      {results.map((item, index) => (
        <div key={`${item.filename}-${index}`} style={{ border: "1px solid #ddd", padding: 12, marginBottom: 12 }}>
          <div>
            <strong>{index + 1}. {item.filename}</strong>
            <span style={{ marginLeft: 8, color: "#888" }}>score: {item.score.toFixed(4)}</span>
          </div>
          <p style={{ whiteSpace: "pre-wrap" }}>{item.text.slice(0, 500)}</p>
        </div>
      ))}
    </section>
  );
}
