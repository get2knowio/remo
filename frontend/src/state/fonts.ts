// Uploaded Nerd Font registration + persistence.
//
// Browsers can't read fonts installed on the remote instance, and patched
// Nerd Fonts carry the Powerline/Git/devicon glyphs a prompt and Zellij status
// bar expect. So the Settings page lets the user upload a patched font once;
// we register it as a `FontFace` (no network — CSP-safe) and persist its bytes
// in IndexedDB (too large for localStorage) so it survives reloads. The
// registered family name becomes selectable as a terminal font.

const DB_NAME = "remo-web-fonts";
const STORE = "fonts";
const DB_VERSION = 1;

export interface StoredFont {
  name: string;
  buffer: ArrayBuffer;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "name" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("indexedDB open failed"));
  });
}

function tx<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const store = db.transaction(STORE, mode).objectStore(STORE);
        const req = run(store);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error ?? new Error("indexedDB request failed"));
      }),
  );
}

/** Derive a usable font-family name from an uploaded file's name. */
function familyFromFilename(filename: string): string {
  const base = filename.replace(/\.(ttf|otf|woff2?|)$/i, "");
  // "JetBrainsMonoNerdFont-Regular" -> "JetBrainsMonoNerdFont Regular"; good
  // enough as a unique, human-readable family name for the picker.
  return base.replace(/[-_]+/g, " ").trim() || "Uploaded Font";
}

async function registerFace(name: string, buffer: ArrayBuffer): Promise<void> {
  const face = new FontFace(name, buffer);
  await face.load();
  document.fonts.add(face);
}

/**
 * Register an uploaded font: read its bytes, load a FontFace, persist to
 * IndexedDB, and return the family name to select in Settings. Rejects if the
 * file isn't a usable font.
 */
export async function registerUploadedFont(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const name = familyFromFilename(file.name);
  await registerFace(name, buffer);
  // Persist a copy of the buffer (arrayBuffer() may be transferred/detached by
  // FontFace on some engines, so re-read from a fresh Blob slice for storage).
  const stored = await file.arrayBuffer();
  await tx("readwrite", (store) => store.put({ name, buffer: stored } satisfies StoredFont));
  return name;
}

/** Re-register every previously-uploaded font at startup. Never rejects. */
export async function restoreUploadedFonts(): Promise<string[]> {
  try {
    const all = await tx<StoredFont[]>("readonly", (store) => store.getAll() as IDBRequest<StoredFont[]>);
    const names: string[] = [];
    for (const f of all) {
      try {
        await registerFace(f.name, f.buffer);
        names.push(f.name);
      } catch (error) {
        console.error("fonts: failed to re-register", f.name, error);
      }
    }
    return names;
  } catch (error) {
    console.error("fonts: failed to restore uploaded fonts", error);
    return [];
  }
}

/** List the names of all uploaded fonts currently in IndexedDB. */
export async function listUploadedFonts(): Promise<string[]> {
  try {
    const all = await tx<StoredFont[]>("readonly", (store) => store.getAll() as IDBRequest<StoredFont[]>);
    return all.map((f) => f.name);
  } catch {
    return [];
  }
}
