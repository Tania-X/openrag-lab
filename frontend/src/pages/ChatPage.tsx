import { useState } from "react";
import { chat } from "../lib/api";

export function ChatPage() {
  const [message, setMessage] = useState("");
  const [response, setResponse] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim()) return;
    setLoading(true);
    setError("");
    try {
      const data = await chat(message);
      setResponse(data.response);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <h2>Chat</h2>
      <form onSubmit={handleSubmit} className="input-row">
        <input
          className="text-input"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="输入问题..."
        />
        <button className="btn" type="submit" disabled={loading}>
          {loading ? "发送中..." : "发送"}
        </button>
      </form>
      {error && <p className="error-text">{error}</p>}
      {response && <div className="answer-block">{response}</div>}
    </section>
  );
}
