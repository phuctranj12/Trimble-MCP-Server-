import { useCallback, useEffect, useState } from "react";
import { api, ApiError, McpKey, Me, ProjectList } from "./api";

const LOGIN_ERRORS: Record<string, string> = {
  invalid_state: "Phiên đăng nhập không hợp lệ. Vui lòng thử lại.",
  expired_state: "Phiên đăng nhập đã hết hạn. Vui lòng thử lại.",
  token_exchange_failed: "Không đổi được mã đăng nhập lấy token từ Trimble.",
  no_identity: "Trimble không trả về danh tính người dùng.",
  access_denied: "Bạn đã từ chối cấp quyền.",
};

const fmtTime = (epoch: number | null) => (epoch ? new Date(epoch * 1000).toLocaleString("vi-VN") : "—");

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadMe = useCallback(() => {
    api.me().then(setMe).catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const loginError = params.get("login_error");
    if (loginError) {
      setError(LOGIN_ERRORS[loginError] ?? `Đăng nhập thất bại (${loginError})`);
      window.history.replaceState(null, "", window.location.pathname);
    }
    loadMe();
  }, [loadMe]);

  return (
    <div className="page">
      <header className="topbar">
        <h1>Trimble Connect MCP</h1>
        {me?.signed_in && (
          <div className="who">
            <span>{me.user.name ?? me.user.email ?? me.user.id}</span>
            <button className="ghost" onClick={() => api.logout().then(loadMe)}>
              Đăng xuất
            </button>
          </div>
        )}
      </header>

      {error && (
        <div className="alert" role="alert">
          {error}
          <button className="ghost" onClick={() => setError(null)} aria-label="Đóng">×</button>
        </div>
      )}

      {me === null && <p className="muted">Đang tải…</p>}
      {me && !me.signed_in && <SignIn />}
      {me?.signed_in && !me.trimble_connected && <SignIn reconnect />}
      {me?.signed_in && me.trimble_connected && (
        <>
          <Connection me={me} onChange={loadMe} onError={setError} />
          <Projects onError={setError} onNeedLogin={loadMe} />
          <McpKeys onError={setError} />
        </>
      )}
    </div>
  );
}

function SignIn({ reconnect = false }: { reconnect?: boolean }) {
  return (
    <section className="card center">
      <h2>{reconnect ? "Kết nối lại Trimble" : "Đăng nhập"}</h2>
      <p className="muted">
        Dùng tài khoản Trimble của chính bạn. Claude chỉ thấy các dự án mà tài khoản này được cấp quyền.
      </p>
      <a className="button" href="/auth/trimble/start">
        Đăng nhập bằng Trimble ID
      </a>
    </section>
  );
}

function Connection({ me, onChange, onError }: { me: Extract<Me, { signed_in: true }>; onChange: () => void; onError: (m: string) => void }) {
  const disconnect = async () => {
    if (!confirm("Ngắt kết nối Trimble? Các khóa MCP sẽ không đọc được dữ liệu cho tới khi kết nối lại.")) return;
    try {
      await api.disconnect();
      onChange();
    } catch (e) {
      onError((e as Error).message);
    }
  };
  return (
    <section className="card row">
      <div>
        <h2>Kết nối Trimble</h2>
        <p className="muted">
          Đã kết nối · {me.user.email ?? me.user.id} · access token hết hạn {fmtTime(me.token_expires_at)} (tự làm mới)
        </p>
      </div>
      <button className="ghost danger" onClick={disconnect}>Ngắt kết nối</button>
    </section>
  );
}

function Projects({ onError, onNeedLogin }: { onError: (m: string) => void; onNeedLogin: () => void }) {
  const [data, setData] = useState<ProjectList | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.projects());
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onNeedLogin();
      onError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [onError, onNeedLogin]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="card">
      <div className="row">
        <h2>Dự án của bạn</h2>
        <button className="ghost" onClick={load} disabled={loading}>
          {loading ? "Đang tải…" : "Tải lại"}
        </button>
      </div>
      {data && (
        <>
          <p className="muted">
            {data.count} dự án · nguồn {data.source} · lấy lúc {new Date(data.fetched_at).toLocaleString("vi-VN")}
          </p>
          {data.region_errors.length > 0 && (
            <p className="warn">
              Một số region lỗi: {data.region_errors.map((r) => `${r.region} (${r.code})`).join(", ")}
            </p>
          )}
          {data.count === 0 ? (
            <p className="muted">Tài khoản này chưa có dự án nào.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Tên dự án</th>
                    <th>Region</th>
                    <th>Project ID</th>
                  </tr>
                </thead>
                <tbody>
                  {data.projects.map((p) => (
                    <tr key={p.id}>
                      <td>{p.name ?? "—"}</td>
                      <td>{p.region ?? "—"}</td>
                      <td><code>{p.id}</code></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function McpKeys({ onError }: { onError: (m: string) => void }) {
  const [keys, setKeys] = useState<McpKey[]>([]);
  const [label, setLabel] = useState("");
  const [newKey, setNewKey] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listKeys().then((r) => setKeys(r.keys)).catch((e) => onError(e.message));
  }, [onError]);

  useEffect(load, [load]);

  const create = async () => {
    try {
      const r = await api.createKey(label.trim() || "Claude");
      setNewKey(r.key);
      setLabel("");
      load();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const remove = async (id: string) => {
    if (!confirm("Thu hồi khóa này? Client đang dùng khóa sẽ mất quyền truy cập.")) return;
    try {
      await api.deleteKey(id);
      load();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const mcpUrl = `${window.location.protocol}//${window.location.hostname}:8000/mcp/`;
  const snippet = JSON.stringify(
    {
      mcpServers: {
        "trimble-connect": {
          command: "npx",
          args: ["mcp-remote", mcpUrl, "--header", "Authorization:Bearer ${TRIMBLE_MCP_KEY}"],
          env: { TRIMBLE_MCP_KEY: newKey ?? "<khóa MCP của bạn>" },
        },
      },
    },
    null,
    2,
  );

  return (
    <section className="card">
      <h2>Khóa MCP cho Claude</h2>
      <p className="muted">
        Khóa MCP xác định bạn là ai khi Claude gọi server. Mỗi khóa gắn với tài khoản Trimble của bạn; không chia sẻ cho người khác.
      </p>

      <div className="row gap">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Tên gợi nhớ, ví dụ: Claude Desktop laptop"
          maxLength={100}
        />
        <button onClick={create}>Tạo khóa</button>
      </div>

      {newKey && (
        <div className="newkey">
          <strong>Khóa mới (chỉ hiện một lần):</strong>
          <div className="row gap">
            <code className="secret">{newKey}</code>
            <button className="ghost" onClick={() => navigator.clipboard.writeText(newKey)}>Sao chép</button>
          </div>
        </div>
      )}

      {keys.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Tên</th>
                <th>Tạo lúc</th>
                <th>Dùng lần cuối</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id}>
                  <td>{k.label ?? "—"}</td>
                  <td>{fmtTime(k.created_at)}</td>
                  <td>{fmtTime(k.last_used_at)}</td>
                  <td><button className="ghost danger" onClick={() => remove(k.id)}>Thu hồi</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <details>
        <summary>Cấu hình Claude Desktop</summary>
        <p className="muted">Endpoint MCP: <code>{mcpUrl}</code></p>
        <pre>{snippet}</pre>
      </details>
    </section>
  );
}
