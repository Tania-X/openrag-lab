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
      <form onSubmit={handleSubmit} className="input-row">
        <input
          className="text-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="输入检索词..."
        />
        <label className="checkbox-label">
          <input type="checkbox" checked={rerank} onChange={(e) => setRerank(e.target.checked)} />
          Rerank
        </label>
        <button className="btn" type="submit" disabled={loading}>
          {loading ? "检索中..." : "检索"}
        </button>
      </form>
      {error && <p className="error-text">{error}</p>}
      {results.map((item, index) => (
        <div key={`${item.filename}-${index}`} className="card">
          <div className="card-title">
            <span>{index + 1}. {item.filename}</span>
            <span className="card-score">score: {item.score.toFixed(4)}</span>
          </div>
          <p className="card-text">{item.text.slice(0, 500)}</p>
        </div>
      ))}
    </section>
  );
}
