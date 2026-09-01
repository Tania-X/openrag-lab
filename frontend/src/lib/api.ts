export interface SearchResult {
  filename: string;
  text: string;
  score: number;
  page?: number | null;
  mimetype?: string | null;
}

export interface SearchResponse {
  results: SearchResult[];
}

export interface ChatResponse {
  response: string;
  chat_id?: string | null;
  sources?: SearchResult[];
}

export interface FileRecord {
  filename: string;
  mimetype: string;
  chunk_count: number;
  embedding_model: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export function search(query: string, rerank = false): Promise<SearchResponse> {
  return request<SearchResponse>("/api/search", {
    method: "POST",
    body: JSON.stringify({ query, limit: 10, rerank }),
  });
}

export function chat(message: string): Promise<ChatResponse> {
  return request<ChatResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message, limit: 10 }),
  });
}

export function listDocuments(): Promise<{ total: number; files: FileRecord[] }> {
  return request<{ total: number; files: FileRecord[] }>("/api/documents");
}
