import { useEffect, useState } from "react";
import { listDocuments, FileRecord } from "../lib/api";

export function DocumentsPage() {
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    listDocuments()
      .then((data) => setFiles(data.files))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <section>
      <h2>Documents</h2>
      {loading && <p>加载中...</p>}
      {error && <p style={{ color: "red" }}>{error}</p>}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th>文件名</th>
            <th>类型</th>
            <th>Chunks</th>
            <th>Embedding</th>
          </tr>
        </thead>
        <tbody>
          {files.map((file) => (
            <tr key={file.filename}>
              <td>{file.filename}</td>
              <td>{file.mimetype}</td>
              <td>{file.chunk_count}</td>
              <td>{file.embedding_model}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
