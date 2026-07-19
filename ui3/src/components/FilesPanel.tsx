import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../state/store";
import { extColor, extOf, fmtSize } from "../lib/format";

// A file entry flattened from the API (path may contain "/"), plus a per-row
// action rendered by the caller (download for session outputs, delete for
// library files).
interface Row {
  path: string;
  size: number;
}

interface TreeNode {
  name: string;
  path: string;
  size: number; // file size, or aggregate for a folder
  isFile: boolean;
  children: TreeNode[];
}

// Build a folder tree from flat "a/b/c.ext" paths. Folders sort before files;
// both alphabetically. Aggregate sizes bubble up so a folder shows its total.
function buildTree(rows: Row[]): TreeNode {
  const root: TreeNode = { name: "", path: "", size: 0, isFile: false, children: [] };
  for (const r of rows) {
    const parts = r.path.split("/").filter(Boolean);
    let node = root;
    parts.forEach((seg, i) => {
      const isLeaf = i === parts.length - 1;
      const childPath = parts.slice(0, i + 1).join("/");
      let child = node.children.find((c) => c.name === seg && c.isFile === isLeaf);
      if (!child) {
        child = { name: seg, path: childPath, size: 0, isFile: isLeaf, children: [] };
        node.children.push(child);
      }
      if (isLeaf) child.size = r.size;
      node = child;
    });
  }
  const sum = (n: TreeNode): number => {
    if (n.isFile) return n.size;
    n.size = n.children.reduce((t, c) => t + sum(c), 0);
    return n.size;
  };
  sum(root);
  const sortRec = (n: TreeNode) => {
    n.children.sort((a, b) =>
      a.isFile === b.isFile ? a.name.localeCompare(b.name) : a.isFile ? 1 : -1,
    );
    n.children.forEach(sortRec);
  };
  sortRec(root);
  return root;
}

function FolderTree({
  node,
  depth,
  action,
}: {
  node: TreeNode;
  depth: number;
  action: (path: string) => React.ReactNode;
}) {
  return (
    <>
      {node.children.map((c) =>
        c.isFile ? (
          <FileRow key={c.path} node={c} depth={depth} action={action} />
        ) : (
          <FolderRow key={c.path} node={c} depth={depth} action={action} />
        ),
      )}
    </>
  );
}

function FolderRow({ node, depth, action }: { node: TreeNode; depth: number; action: (p: string) => React.ReactNode }) {
  const [open, setOpen] = useState(depth < 1);
  const count = node.children.length;
  return (
    <>
      <div
        onClick={() => setOpen((o) => !o)}
        style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", paddingLeft: 8 + depth * 14, borderRadius: 7, cursor: "pointer" }}
      >
        <span style={{ color: "var(--lo)", fontSize: 9, width: 8, flex: "0 0 auto" }}>{open ? "▾" : "▸"}</span>
        <span style={{ fontSize: 13, flex: "0 0 auto" }}>{open ? "📂" : "📁"}</span>
        <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, color: "var(--hi)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.name}</span>
        <span style={{ fontSize: 10, color: "var(--lo)" }}>{count} {count === 1 ? "item" : "items"}</span>
        <span style={{ fontSize: 10, color: "var(--lo)", fontFamily: "var(--mono)" }}>{fmtSize(node.size)}</span>
      </div>
      {open && <FolderTree node={node} depth={depth + 1} action={action} />}
    </>
  );
}

function FileRow({ node, depth, action }: { node: TreeNode; depth: number; action: (p: string) => React.ReactNode }) {
  const ext = extOf(node.name);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "7px 8px", paddingLeft: 8 + depth * 14, borderRadius: 7 }}>
      <span style={{ width: 8, flex: "0 0 auto" }} />
      <span style={{ fontFamily: "var(--mono)", fontSize: 9.5, fontWeight: 600, padding: "2px 5px", borderRadius: 4, background: "var(--bg3)", color: extColor(ext), flex: "0 0 auto" }}>{ext}</span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: "block", fontFamily: "var(--mono)", fontSize: 12, color: "var(--hi)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.name}</span>
        <span style={{ fontSize: 10, color: "var(--lo)" }}>{fmtSize(node.size)}</span>
      </span>
      {action(node.path)}
    </div>
  );
}

export default function FilesPanel() {
  const { files, loadFiles, downloadFile, library, libraryHealth, loadLibrary, uploadFiles, deleteLibrary } = useApp();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadFiles();
    loadLibrary();
  }, [loadFiles, loadLibrary]);

  // The directory-picker attributes aren't in React's typed props; set them
  // imperatively so a click selects a whole folder (webkitRelativePath is then
  // populated on each File).
  useEffect(() => {
    const el = dirInputRef.current;
    if (el) {
      el.setAttribute("webkitdirectory", "");
      el.setAttribute("directory", "");
    }
  }, []);

  const sessionTree = useMemo(() => buildTree(files.map((f) => ({ path: f.path, size: f.size }))), [files]);
  const libraryTree = useMemo(() => buildTree(library.map((f) => ({ path: f.name, size: f.size }))), [library]);

  const usedPct = libraryHealth
    ? Math.min(100, Math.round((libraryHealth.used_bytes / (libraryHealth.used_bytes + libraryHealth.free_bytes)) * 100))
    : 0;

  const getAction = (path: string) => (
    <button onClick={() => downloadFile(path)} style={{ fontSize: 11, color: "var(--accent)", fontFamily: "var(--mono)", padding: "4px 9px", borderRadius: 6, border: "1px solid var(--border)", flex: "0 0 auto" }}>
      get
    </button>
  );
  const delAction = (path: string) => (
    <button onClick={() => void deleteLibrary(path)} style={{ fontSize: 12, color: "var(--lo)", padding: "3px 7px", flex: "0 0 auto" }}>
      ✕
    </button>
  );

  return (
    <div style={{ padding: "14px 16px 20px" }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em", marginBottom: 9 }}>
        Generated by this session
      </div>
      {files.length === 0 && <div style={{ fontSize: 12, color: "var(--lo)", marginBottom: 8 }}>No files yet.</div>}
      {files.length > 0 && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 9, padding: "4px 2px", background: "var(--bg2)" }}>
          <FolderTree node={sessionTree} depth={0} action={getAction} />
        </div>
      )}

      <div style={{ fontSize: 11, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em", margin: "18px 0 9px" }}>
        Shared library
      </div>
      <div style={{ display: "flex", gap: 7, marginBottom: 9 }}>
        <label style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 6, padding: "10px 8px", border: "1px dashed var(--border-hi)", borderRadius: 9, color: "var(--mid)", fontSize: 12, cursor: "pointer" }}>
          <span style={{ fontSize: 14 }}>↑</span> Files
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={(e) => {
              if (e.target.files) void uploadFiles(e.target.files);
              if (fileInputRef.current) fileInputRef.current.value = "";
            }}
            style={{ display: "none" }}
          />
        </label>
        <label style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 6, padding: "10px 8px", border: "1px dashed var(--border-hi)", borderRadius: 9, color: "var(--mid)", fontSize: 12, cursor: "pointer" }}>
          <span style={{ fontSize: 14 }}>📁</span> Folder
          <input
            ref={dirInputRef}
            type="file"
            multiple
            onChange={(e) => {
              if (e.target.files) void uploadFiles(e.target.files);
              if (dirInputRef.current) dirInputRef.current.value = "";
            }}
            style={{ display: "none" }}
          />
        </label>
      </div>

      {library.length === 0 && <div style={{ fontSize: 12, color: "var(--lo)", marginBottom: 8 }}>Library is empty.</div>}
      {library.length > 0 && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 9, padding: "4px 2px", background: "var(--bg2)" }}>
          <FolderTree node={libraryTree} depth={0} action={delAction} />
        </div>
      )}

      {libraryHealth && (
        <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "var(--lo)" }}>
          <span style={{ flex: 1, height: 4, borderRadius: 3, background: "var(--bg3)", overflow: "hidden" }}>
            <span style={{ display: "block", width: `${usedPct}%`, height: "100%", background: "var(--accent)" }} />
          </span>
          <span style={{ fontFamily: "var(--mono)" }}>
            {fmtSize(libraryHealth.used_bytes)} used · {fmtSize(libraryHealth.free_bytes)} free
          </span>
        </div>
      )}
    </div>
  );
}
